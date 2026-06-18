#!/usr/bin/env python3
"""send-email runtime: send mail over SMTP using only the Python standard library.

Subcommands:
  send         compose and send a message (text and/or HTML, attachments, cc/bcc, reply-to)
  verify       connect and authenticate to the SMTP server, then disconnect (sends nothing)
  show-config  print the resolved configuration with the password redacted
  selftest     offline smoke (no network): build, serialize, and re-parse messages in memory

Configuration is resolved in increasing precedence from (1) a JSON secrets file
named by AAS_SECRETS_FILE (its "smtp" object, or top-level SMTP_* keys),
(2) environment variables (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
SMTP_FROM, SMTP_SECURITY, SMTP_TIMEOUT), then (3) explicit command-line flags.
Credentials are never printed and are redacted out of error messages.

Invoke via the managed runner, e.g.:
  bash ~/.local/share/ai-agents-skills/runtime/run_skill.sh \
    skills/send-email/run_send_email.sh send --to <recipient> --subject "Hi" --body "Hello"
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import smtplib
import ssl
import sys
import tempfile
from dataclasses import dataclass
from email import message_from_bytes
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid, parseaddr
from pathlib import Path

DEFAULT_TIMEOUT = 30
VALID_SECURITY = ("ssl", "starttls", "plain")


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _fail(command: str, error_code: str, message: str) -> int:
    _emit({"ok": False, "command": command, "error_code": error_code, "message": message})
    return 1


@dataclass
class SmtpConfig:
    """Resolved SMTP connection settings; port/security default from one another."""

    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = None
    sender: str | None = None
    security: str | None = None
    timeout: int = DEFAULT_TIMEOUT
    allow_insecure_auth: bool = False

    def resolved_security(self) -> str:
        if self.security:
            return self.security
        if self.port == 465:
            return "ssl"
        if self.port == 25:
            return "plain"
        return "starttls"

    def resolved_port(self) -> int:
        if self.port:
            return self.port
        if self.security == "ssl":
            return 465
        if self.security == "plain":
            return 25
        return 587


def _coerce_int(value: object, default: int | None = None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _secrets_path() -> str | None:
    return os.environ.get("AAS_SECRETS_FILE") or os.environ.get("OPENCLAW_SECRETS_FILE")


def _load_secrets() -> dict:
    """Return SMTP settings from the secrets file, normalized to bare lowercase keys."""
    path = _secrets_path()
    if not path:
        return {}
    file = Path(path)
    if not file.is_file():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    smtp = data.get("smtp")
    raw = smtp if isinstance(smtp, dict) else data
    out: dict = {}
    for key, value in raw.items():
        norm = str(key).lower()
        if norm.startswith("smtp_"):
            norm = norm[len("smtp_") :]
        out[norm] = value
    return out


def load_config(args: argparse.Namespace) -> SmtpConfig:
    secrets = _load_secrets()

    def pick(cli_value: object, env_key: str, *secret_keys: str) -> object:
        if cli_value is not None:
            return cli_value
        env_value = os.environ.get(env_key)
        if env_value not in (None, ""):
            return env_value
        for secret_key in secret_keys:
            value = secrets.get(secret_key)
            if value not in (None, ""):
                return value
        return None

    host = pick(getattr(args, "host", None), "SMTP_HOST", "host")
    port = pick(getattr(args, "port", None), "SMTP_PORT", "port")
    user = pick(getattr(args, "user", None), "SMTP_USER", "user", "username")
    password = pick(getattr(args, "password", None), "SMTP_PASSWORD", "password", "pass")
    sender = pick(getattr(args, "sender", None), "SMTP_FROM", "from", "sender")
    security = pick(getattr(args, "security", None), "SMTP_SECURITY", "security")
    timeout = pick(getattr(args, "timeout", None), "SMTP_TIMEOUT", "timeout")

    return SmtpConfig(
        host=str(host) if host is not None else None,
        port=_coerce_int(port),
        user=str(user) if user is not None else None,
        password=str(password) if password is not None else None,
        sender=str(sender) if sender is not None else None,
        security=str(security) if security is not None else None,
        timeout=_coerce_int(timeout, DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT,
        allow_insecure_auth=bool(getattr(args, "allow_insecure_auth", False)),
    )


def _no_newline(value: str | None, field: str) -> str | None:
    """Reject header values containing CR/LF to block header injection."""
    if value and ("\n" in value or "\r" in value):
        raise ValueError(f"illegal newline in {field}")
    return value


def _split_addresses(values: list[str] | None) -> list[str]:
    """Flatten repeated and comma-separated address arguments into bare addresses."""
    out: list[str] = []
    for raw in values or []:
        _no_newline(raw, "recipient")
        for _name, addr in getaddresses([raw]):
            addr = addr.strip()
            if addr:
                out.append(addr)
    return out


def _read_body(inline: str | None, file_path: str | None) -> str | None:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")
    if inline is not None:
        return inline
    return None


def _attach_file(msg: EmailMessage, path_str: str) -> None:
    path = Path(path_str)
    if not path.is_file():
        raise ValueError(f"attachment not found: {path_str}")
    ctype, encoding = mimetypes.guess_type(path.name)
    if ctype is None or encoding is not None:
        ctype = "application/octet-stream"
    maintype, subtype = ctype.split("/", 1)
    msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)


def build_message(args: argparse.Namespace, cfg: SmtpConfig) -> EmailMessage:
    """Compose an EmailMessage from CLI args; text+html becomes multipart/alternative."""
    sender = _no_newline(args.sender or cfg.sender, "from")
    if not sender:
        raise ValueError("no sender address: set --from or SMTP_FROM")

    msg = EmailMessage()
    msg["From"] = sender
    to_list = args.to or []
    cc_list = args.cc or []
    for value in to_list + cc_list:
        _no_newline(value, "recipient")
    if to_list:
        msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = _no_newline(args.subject or "", "subject")
    reply_to = _no_newline(getattr(args, "reply_to", None), "reply-to")
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Date"] = formatdate(localtime=True)
    # Always pass an explicit domain: make_msgid() with domain=None falls back to
    # socket.getfqdn(), which leaks the local hostname and can block on slow
    # reverse-DNS (it also breaks the offline contract). Use the sender's domain.
    msg["Message-ID"] = make_msgid(domain=_sender_domain(sender) or "localhost")

    text = _read_body(args.body, args.body_file)
    html = _read_body(args.html, args.html_file)
    if text is None and html is None:
        text = ""
    if text is not None:
        msg.set_content(text)
    if html is not None:
        if text is None:
            msg.set_content("This message requires an HTML-capable email client.")
        msg.add_alternative(html, subtype="html")

    for attach in args.attach or []:
        _attach_file(msg, attach)
    return msg


def _envelope_recipients(args: argparse.Namespace) -> list[str]:
    return _split_addresses((args.to or []) + (args.cc or []) + (getattr(args, "bcc", None) or []))


def _redact(text: str, cfg: SmtpConfig) -> str:
    if cfg.password:
        text = text.replace(cfg.password, "***")
    return text


def _sender_domain(sender: str | None) -> str | None:
    """Return the sender's domain for the Message-ID, never the local hostname."""
    if not sender:
        return None
    addr = parseaddr(sender)[1]
    if "@" not in addr:
        return None
    return addr.rsplit("@", 1)[-1] or None


def _auth_guard(cfg: SmtpConfig) -> str | None:
    """Refuse SMTP AUTH over an unencrypted connection unless explicitly allowed."""
    if cfg.user and cfg.resolved_security() == "plain" and not cfg.allow_insecure_auth:
        return ("refusing to send credentials over an unencrypted connection; "
                "use --security ssl or starttls, or pass --allow-insecure-auth")
    return None


def _connect(cfg: SmtpConfig) -> smtplib.SMTP:
    if not cfg.host:
        raise ValueError("no SMTP host: set --host or SMTP_HOST")
    security = cfg.resolved_security()
    port = cfg.resolved_port()
    context = ssl.create_default_context()
    server: smtplib.SMTP
    if security == "ssl":
        server = smtplib.SMTP_SSL(cfg.host, port, timeout=cfg.timeout, context=context)
    else:
        server = smtplib.SMTP(cfg.host, port, timeout=cfg.timeout)
        if security == "starttls":
            server.starttls(context=context)
    if cfg.user:
        server.login(cfg.user, cfg.password or "")
    return server


def _message_summary(msg: EmailMessage, recipients: list[str]) -> dict:
    return {
        "from": parseaddr(msg["From"])[1],
        "recipients": recipients,
        "subject": msg["Subject"],
        "has_html": any(part.get_content_type() == "text/html" for part in msg.walk()),
        "attachments": [att.get_filename() for att in msg.iter_attachments()],
        "message_id": msg["Message-ID"],
    }


def cmd_send(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    try:
        msg = build_message(args, cfg)
    except (ValueError, OSError) as exc:
        return _fail("send", "build_failed", str(exc))

    recipients = _envelope_recipients(args)
    if not recipients:
        return _fail("send", "no_recipients", "no recipients: pass --to, --cc, or --bcc")

    summary = {"ok": True, "command": "send", **_message_summary(msg, recipients)}
    if args.dry_run:
        summary["dry_run"] = True
        summary["bytes"] = len(bytes(msg))
        _emit(summary)
        return 0

    if not cfg.host:
        return _fail("send", "no_host", "no SMTP host: set --host or SMTP_HOST")
    guard = _auth_guard(cfg)
    if guard:
        return _fail("send", "insecure_auth", guard)
    try:
        server = _connect(cfg)
    except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
        return _fail("send", "connect_failed", _redact(str(exc), cfg))
    try:
        server.send_message(msg, from_addr=parseaddr(msg["From"])[1], to_addrs=recipients)
    except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
        return _fail("send", "send_failed", _redact(str(exc), cfg))
    finally:
        try:
            server.quit()
        except (smtplib.SMTPException, OSError):
            pass
    _emit(summary)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    if not cfg.host:
        return _fail("verify", "no_host", "no SMTP host: set --host or SMTP_HOST")
    guard = _auth_guard(cfg)
    if guard:
        return _fail("verify", "insecure_auth", guard)
    try:
        server = _connect(cfg)
    except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
        return _fail("verify", "connect_failed", _redact(str(exc), cfg))
    try:
        server.noop()
    except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
        return _fail("verify", "verify_failed", _redact(str(exc), cfg))
    finally:
        try:
            server.quit()
        except (smtplib.SMTPException, OSError):
            pass
    _emit({
        "ok": True,
        "command": "verify",
        "host": cfg.host,
        "port": cfg.resolved_port(),
        "security": cfg.resolved_security(),
        "authenticated": bool(cfg.user),
    })
    return 0


def cmd_show_config(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    path = _secrets_path()
    _emit({
        "ok": True,
        "command": "show-config",
        "host": cfg.host,
        "port": cfg.resolved_port() if cfg.host else cfg.port,
        "security": cfg.resolved_security() if cfg.host else cfg.security,
        "user": cfg.user,
        "from": cfg.sender,
        "timeout": cfg.timeout,
        "password_set": bool(cfg.password),
        "secrets_file": path,
        "secrets_file_present": bool(path and Path(path).is_file()),
    })
    return 0


def _selftest_namespace(**overrides: object) -> argparse.Namespace:
    base = {
        "host": None, "port": None, "user": None, "password": None, "security": None,
        "timeout": None, "sender": "<sender-address>", "to": ["<recipient>"], "cc": [],
        "bcc": [], "subject": "Self test", "body": None, "body_file": None, "html": None,
        "html_file": None, "attach": [], "reply_to": None, "dry_run": True,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def cmd_selftest(args: argparse.Namespace) -> int:
    checks: list[dict] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    cfg = SmtpConfig()

    # 1. Plain-text message round-trips through serialization.
    plain = build_message(_selftest_namespace(body="hello"), cfg)
    reparsed = message_from_bytes(bytes(plain))
    record("plain_text", reparsed.get_content_type() == "text/plain",
           reparsed.get_content_type())

    # 2. Text + HTML yields a multipart/alternative with both parts.
    multi = build_message(_selftest_namespace(body="hi", html="<p>hi</p>"), cfg)
    types = {part.get_content_type() for part in multi.walk()}
    record("text_and_html", {"text/plain", "text/html"} <= types, ",".join(sorted(types)))

    # 3. Attachments produce a retrievable attachment with the right filename.
    with tempfile.TemporaryDirectory() as tmp:
        attach_path = Path(tmp) / "note.txt"
        attach_path.write_text("attachment body", encoding="utf-8")
        attached = build_message(_selftest_namespace(body="see file", attach=[str(attach_path)]), cfg)
        names = [a.get_filename() for a in attached.iter_attachments()]
        record("attachment", names == ["note.txt"], ",".join(names))

    # 4. cc/bcc expand the envelope but bcc never appears in the headers.
    routed = _selftest_namespace(to=["<a>"], cc=["<b>"], bcc=["<c>"])
    msg = build_message(routed, cfg)
    recipients = _envelope_recipients(routed)
    record("envelope_cc_bcc", set(recipients) == {"a", "b", "c"} and msg["Bcc"] is None,
           ",".join(sorted(recipients)))

    # 5. Port/security inference is consistent.
    record("security_ssl_465", SmtpConfig(port=465).resolved_security() == "ssl")
    record("security_starttls_default", SmtpConfig().resolved_security() == "starttls"
           and SmtpConfig().resolved_port() == 587)
    record("port_for_ssl", SmtpConfig(security="ssl").resolved_port() == 465)

    # 6. Header-injection attempts are rejected.
    try:
        build_message(_selftest_namespace(subject="ok\r\nBcc: <intruder>"), cfg)
        injection_blocked = False
    except ValueError:
        injection_blocked = True
    record("header_injection_blocked", injection_blocked)

    # 7. Passwords are redacted out of error text.
    record("password_redaction",
           "secret" not in _redact("login failed for secret", SmtpConfig(password="secret")))

    # 8. AUTH over an unencrypted connection is refused unless explicitly allowed.
    record("auth_guard_blocks_plain",
           _auth_guard(SmtpConfig(user="u", security="plain")) is not None
           and _auth_guard(SmtpConfig(user="u", security="plain", allow_insecure_auth=True)) is None
           and _auth_guard(SmtpConfig(user="u", security="starttls")) is None)

    # 9. The Message-ID domain comes from the sender, not the local hostname.
    sample_addr = "noreply" + "@" + "list.example"
    record("message_id_uses_sender_domain",
           _sender_domain(sample_addr) == "list.example" and _sender_domain("nodomain") is None)

    if args.work_dir:
        work = Path(args.work_dir)
        work.mkdir(parents=True, exist_ok=True)
        (work / "selftest.eml").write_bytes(bytes(plain))

    passed = sum(1 for c in checks if c["ok"])
    failed = len(checks) - passed
    _emit({
        "ok": failed == 0,
        "command": "selftest",
        "passed": passed,
        "failed": failed,
        "checks": checks,
    })
    return 0 if failed == 0 else 1


def _add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", help="SMTP server host (or set SMTP_HOST)")
    parser.add_argument("--port", type=int, help="SMTP server port (default 587, or 465 for ssl)")
    parser.add_argument("--user", help="SMTP username (or set SMTP_USER)")
    parser.add_argument("--password", help="SMTP password (prefer SMTP_PASSWORD or the secrets file)")
    parser.add_argument("--security", choices=VALID_SECURITY, help="ssl, starttls, or plain")
    parser.add_argument("--timeout", type=int, help="connection timeout in seconds")
    parser.add_argument("--from", dest="sender", help="sender address (or set SMTP_FROM)")
    parser.add_argument("--allow-insecure-auth", dest="allow_insecure_auth", action="store_true",
                        help="permit SMTP AUTH over an unencrypted (plain) connection")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="send-email", description="Send email over SMTP (stdlib only).")
    sub = parser.add_subparsers(dest="command", required=True)

    send = sub.add_parser("send", help="compose and send a message")
    _add_connection_args(send)
    send.add_argument("--to", action="append", help="recipient (repeatable; comma-separated allowed)")
    send.add_argument("--cc", action="append", help="cc recipient (repeatable)")
    send.add_argument("--bcc", action="append", help="bcc recipient (repeatable)")
    send.add_argument("--subject", help="message subject")
    send.add_argument("--body", help="plain-text body")
    send.add_argument("--body-file", dest="body_file", help="read the plain-text body from a file")
    send.add_argument("--html", help="HTML body")
    send.add_argument("--html-file", dest="html_file", help="read the HTML body from a file")
    send.add_argument("--attach", action="append", help="file to attach (repeatable)")
    send.add_argument("--reply-to", dest="reply_to", help="Reply-To address")
    send.add_argument("--dry-run", action="store_true", help="compose and report without sending")
    send.set_defaults(func=cmd_send)

    verify = sub.add_parser("verify", help="connect and authenticate, sending nothing")
    _add_connection_args(verify)
    verify.set_defaults(func=cmd_verify)

    show = sub.add_parser("show-config", help="print the resolved config with the password redacted")
    _add_connection_args(show)
    show.set_defaults(func=cmd_show_config)

    selftest = sub.add_parser("selftest", help="offline smoke (no network)")
    selftest.add_argument("--work-dir", dest="work_dir", default=None,
                          help="optional scratch directory for a sample .eml artifact")
    selftest.set_defaults(func=cmd_selftest)
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # backstop: a failure must stay JSON, never a raw traceback
        return _fail(getattr(args, "command", "?"), "unexpected_error", str(exc))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
