#!/usr/bin/env python3
"""send-email runtime: send mail over SMTP using only the Python standard library.

Subcommands:
  send         compose and send a message (text and/or HTML, attachments, cc/bcc, reply-to)
  verify       connect and authenticate to the SMTP server, then disconnect (sends nothing)
  show-config  print the resolved configuration with the password redacted
  selftest     offline smoke (no network): build, serialize, and re-parse messages in memory

Configuration is resolved in increasing precedence from (1) a JSON secrets file
named by AAS_SECRETS_FILE (its "smtp" object, or top-level SMTP_* keys),
(2) environment variables, then (3) explicit command-line flags. Connection
settings: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM,
SMTP_SECURITY, SMTP_TIMEOUT. Pre-defined sender identity (all optional):
SMTP_FROM_NAME, SMTP_REPLY_TO, SMTP_CC, SMTP_BCC, SMTP_SIGNATURE,
SMTP_SIGNATURE_HTML, SMTP_REPLY_TO_SELF, SMTP_BCC_SELF, or the matching keys
(from_name, reply_to, cc, bcc, signature, signature_html, reply_to_self,
bcc_self) in the secrets file's "smtp" object. By default Reply-To and Bcc are
set to the sender address; disable with --no-reply-to-self / --no-bcc-self.
Credentials are never printed and are redacted from error messages.

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
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import EmailMessage
from email.utils import formataddr, formatdate, getaddresses, make_msgid, parseaddr
from html import escape as _html_escape
from pathlib import Path

DEFAULT_TIMEOUT = 30
VALID_SECURITY = ("ssl", "starttls", "plain")
SIGNATURE_DELIMITER = "-- "  # RFC 3676 signature separator (trailing space is intentional)


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _fail(command: str, error_code: str, message: str) -> int:
    _emit({"ok": False, "command": command, "error_code": error_code, "message": message})
    return 1


@dataclass
class SmtpConfig:
    """Resolved SMTP connection settings plus pre-defined sender identity."""

    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = None
    sender: str | None = None
    security: str | None = None
    timeout: int = DEFAULT_TIMEOUT
    allow_insecure_auth: bool = False
    from_name: str | None = None
    reply_to: str | None = None
    signature: str | None = None
    signature_html: str | None = None
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    reply_to_self: bool = True
    bcc_self: bool = True

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


def _coerce_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_list(value: object) -> list[str]:
    """Normalize a list, or a comma/newline-separated string, into a clean list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).replace("\n", ",").split(",") if part.strip()]


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
    from_name = pick(getattr(args, "from_name", None), "SMTP_FROM_NAME", "from_name")
    reply_to = pick(getattr(args, "reply_to", None), "SMTP_REPLY_TO", "reply_to")
    signature = pick(None, "SMTP_SIGNATURE", "signature")
    signature_html = pick(None, "SMTP_SIGNATURE_HTML", "signature_html")

    reply_to_self = _coerce_bool(pick(None, "SMTP_REPLY_TO_SELF", "reply_to_self"), True)
    if getattr(args, "no_reply_to_self", False):
        reply_to_self = False
    bcc_self = _coerce_bool(pick(None, "SMTP_BCC_SELF", "bcc_self"), True)
    if getattr(args, "no_bcc_self", False):
        bcc_self = False

    return SmtpConfig(
        host=str(host) if host is not None else None,
        port=_coerce_int(port),
        user=str(user) if user is not None else None,
        password=str(password) if password is not None else None,
        sender=str(sender) if sender is not None else None,
        security=str(security) if security is not None else None,
        timeout=_coerce_int(timeout, DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT,
        allow_insecure_auth=bool(getattr(args, "allow_insecure_auth", False)),
        from_name=str(from_name) if from_name is not None else None,
        reply_to=str(reply_to) if reply_to is not None else None,
        signature=str(signature) if signature is not None else None,
        signature_html=str(signature_html) if signature_html is not None else None,
        cc=_as_list(os.environ.get("SMTP_CC")) or _as_list(secrets.get("cc")),
        bcc=_as_list(os.environ.get("SMTP_BCC")) or _as_list(secrets.get("bcc")),
        reply_to_self=reply_to_self,
        bcc_self=bcc_self,
    )


def _no_newline(value: str | None, field_name: str) -> str | None:
    """Reject header values containing CR/LF to block header injection."""
    if value and ("\n" in value or "\r" in value):
        raise ValueError(f"illegal newline in {field_name}")
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


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
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


def _apply_from_name(sender: str, from_name: str | None) -> str:
    """Format the From header as 'Name <addr>', unless the sender already names itself."""
    name, addr = parseaddr(sender)
    if name or not from_name or not addr:
        return sender
    return formataddr((from_name, addr))


def _resolved_signature(args: argparse.Namespace, cfg: SmtpConfig) -> str | None:
    if getattr(args, "no_signature", False):
        return None
    if getattr(args, "signature_file", None):
        return Path(args.signature_file).read_text(encoding="utf-8")
    if getattr(args, "signature", None):
        return args.signature
    return cfg.signature


def _resolved_signature_html(args: argparse.Namespace, cfg: SmtpConfig) -> str | None:
    if getattr(args, "no_signature", False):
        return None
    if getattr(args, "signature_html_file", None):
        return Path(args.signature_html_file).read_text(encoding="utf-8")
    return cfg.signature_html


def build_message(args: argparse.Namespace, cfg: SmtpConfig) -> EmailMessage:
    """Compose an EmailMessage from CLI args and pre-defined identity defaults."""
    base_sender = _no_newline(args.sender or cfg.sender, "from")
    if not base_sender:
        raise ValueError("no sender address: set --from or SMTP_FROM")
    sender = _no_newline(_apply_from_name(base_sender, cfg.from_name), "from")
    sender_addr = parseaddr(base_sender)[1]

    msg = EmailMessage()
    msg["From"] = sender
    to_list = args.to or []
    cc_list = list(cfg.cc) + (args.cc or [])
    for value in to_list + cc_list:
        _no_newline(value, "recipient")
    if to_list:
        msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = _no_newline(args.subject or "", "subject")

    reply_to = _no_newline(cfg.reply_to, "reply-to")
    if not reply_to and cfg.reply_to_self:
        reply_to = sender_addr
    if reply_to:
        msg["Reply-To"] = reply_to

    msg["Date"] = formatdate(localtime=True)
    # Always pass an explicit domain: make_msgid() with domain=None falls back to
    # socket.getfqdn(), which leaks the local hostname and can block on slow
    # reverse-DNS (it also breaks the offline contract). Use the sender's domain.
    msg["Message-ID"] = make_msgid(domain=_sender_domain(base_sender) or "localhost")

    text = _read_body(args.body, args.body_file)
    html = _read_body(args.html, args.html_file)
    if text is None and html is None:
        text = ""

    signature = _resolved_signature(args, cfg)
    signature_html = _resolved_signature_html(args, cfg)
    if signature and text is not None:
        text = f"{text}\n\n{SIGNATURE_DELIMITER}\n{signature}"
    if html is not None:
        sig_html = signature_html
        if not sig_html and signature:
            sig_html = "<pre>" + _html_escape(signature) + "</pre>"
        if sig_html:
            html = f"{html}<br><br>{SIGNATURE_DELIMITER}<br>{sig_html}"

    if text is not None:
        msg.set_content(text)
    if html is not None:
        if text is None:
            msg.set_content("This message requires an HTML-capable email client.")
        msg.add_alternative(html, subtype="html")

    for attach in args.attach or []:
        _attach_file(msg, attach)
    return msg


def _envelope_recipients(args: argparse.Namespace, cfg: SmtpConfig) -> list[str]:
    to = args.to or []
    cc = list(cfg.cc) + (args.cc or [])
    bcc = list(cfg.bcc) + (getattr(args, "bcc", None) or [])
    if cfg.bcc_self:
        sender_addr = parseaddr(args.sender or cfg.sender or "")[1]
        if sender_addr:
            bcc.append(sender_addr)
    return _dedupe(_split_addresses(to + cc + bcc))


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
        "from": msg["From"],
        "reply_to": msg["Reply-To"],
        "recipients": recipients,
        "cc": msg["Cc"],
        "subject": msg["Subject"],
        "has_html": any(part.get_content_type() == "text/html" for part in msg.walk()),
        "attachments": [att.get_filename() for att in msg.iter_attachments()],
        "message_id": msg["Message-ID"],
    }


def cmd_send(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    try:
        msg = build_message(args, cfg)
        recipients = _envelope_recipients(args, cfg)
    except (ValueError, OSError) as exc:
        return _fail("send", "build_failed", str(exc))
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
        "from_name": cfg.from_name,
        "reply_to": cfg.reply_to,
        "reply_to_self": cfg.reply_to_self,
        "bcc_self": cfg.bcc_self,
        "default_cc": cfg.cc,
        "default_bcc": cfg.bcc,
        "signature_set": bool(cfg.signature),
        "signature_html_set": bool(cfg.signature_html),
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
        "from_name": None, "signature": None, "signature_file": None,
        "signature_html_file": None, "no_signature": False, "no_reply_to_self": False,
        "no_bcc_self": False, "allow_insecure_auth": False,
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
    msg = build_message(routed, SmtpConfig(bcc_self=False))
    recipients = _envelope_recipients(routed, SmtpConfig(bcc_self=False))
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

    # 10. A pre-defined from_name produces a 'Name <addr>' From header.
    named = build_message(_selftest_namespace(), SmtpConfig(from_name="Test Sender"))
    record("from_name_applied", named["From"] == "Test Sender <sender-address>", named["From"])

    # 11. A pre-defined signature is appended after the standard delimiter.
    signed = build_message(_selftest_namespace(body="Body."), SmtpConfig(signature="Jane\nLab"))
    body_text = signed.get_body(preferencelist=("plain",)).get_content()
    record("signature_appended", body_text.rstrip().endswith("-- \nJane\nLab"), repr(body_text[-24:]))

    # 12. Reply-To and Bcc default to the sender address (and can be disabled).
    on = _selftest_namespace()
    on_msg = build_message(on, SmtpConfig())
    on_env = _envelope_recipients(on, SmtpConfig())
    off = _selftest_namespace(no_reply_to_self=True, no_bcc_self=True)
    off_msg = build_message(off, SmtpConfig(reply_to_self=False, bcc_self=False))
    record("reply_to_and_bcc_self_default",
           on_msg["Reply-To"] == "sender-address" and "sender-address" in on_env
           and off_msg["Reply-To"] is None and "sender-address" not in
           _envelope_recipients(off, SmtpConfig(reply_to_self=False, bcc_self=False)))

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
    parser.add_argument("--from-name", dest="from_name",
                        help="sender display name combined with the address (or set SMTP_FROM_NAME)")
    parser.add_argument("--allow-insecure-auth", dest="allow_insecure_auth", action="store_true",
                        help="permit SMTP AUTH over an unencrypted (plain) connection")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="send-email", description="Send email over SMTP (stdlib only).")
    sub = parser.add_subparsers(dest="command", required=True)

    send = sub.add_parser("send", help="compose and send a message")
    _add_connection_args(send)
    send.add_argument("--to", action="append", help="recipient (repeatable; comma-separated allowed)")
    send.add_argument("--cc", action="append", help="cc recipient (repeatable; adds to defaults)")
    send.add_argument("--bcc", action="append", help="bcc recipient (repeatable; adds to defaults)")
    send.add_argument("--subject", help="message subject")
    send.add_argument("--body", help="plain-text body")
    send.add_argument("--body-file", dest="body_file", help="read the plain-text body from a file")
    send.add_argument("--html", help="HTML body")
    send.add_argument("--html-file", dest="html_file", help="read the HTML body from a file")
    send.add_argument("--attach", action="append", help="file to attach (repeatable)")
    send.add_argument("--reply-to", dest="reply_to", help="Reply-To address (overrides the default)")
    send.add_argument("--signature", help="plain-text signature (overrides the configured one)")
    send.add_argument("--signature-file", dest="signature_file", help="read the plain-text signature from a file")
    send.add_argument("--signature-html-file", dest="signature_html_file",
                      help="read the HTML signature from a file")
    send.add_argument("--no-signature", dest="no_signature", action="store_true",
                      help="do not append any signature")
    send.add_argument("--no-reply-to-self", dest="no_reply_to_self", action="store_true",
                      help="do not default Reply-To to the sender address")
    send.add_argument("--no-bcc-self", dest="no_bcc_self", action="store_true",
                      help="do not bcc the sender address")
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
