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
import io
import json
import mimetypes
import os
import shutil
import smtplib
import ssl
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import message_from_bytes
from email import policy as email_policy
from email.generator import BytesGenerator
from email.message import EmailMessage
from email.utils import formataddr, formatdate, getaddresses, make_msgid, parseaddr
from html import escape as _html_escape
from pathlib import Path

DEFAULT_TIMEOUT = 30
VALID_SECURITY = ("ssl", "starttls", "plain")
SIGNATURE_DELIMITER = "-- "  # RFC 3676 signature separator (trailing space is intentional)
PGP_DIGEST = "SHA256"
PGP_MICALG = "pgp-sha256"  # must match PGP_DIGEST per RFC 3156


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
    pgp_sign: bool = False
    pgp_key: str | None = None
    pgp_passphrase: str | None = None
    gnupg_home: str | None = None
    account: str | None = None
    account_error: str | None = None

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


def _read_secrets_file() -> dict:
    """Return the raw parsed secrets-file object, or {} if absent/unreadable."""
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
    return data if isinstance(data, dict) else {}


def _normalize(raw: dict) -> dict:
    """Lowercase keys and drop the optional smtp_ prefix (so SMTP_HOST == host)."""
    out: dict = {}
    for key, value in raw.items():
        norm = str(key).lower()
        if norm.startswith("smtp_"):
            norm = norm[len("smtp_") :]
        out[norm] = value
    return out


def _account_names(file_data: dict) -> list[str]:
    accounts = file_data.get("accounts")
    return sorted(accounts) if isinstance(accounts, dict) else []


def _select_account(args: argparse.Namespace, file_data: dict) -> tuple[dict, str | None, str | None]:
    """Pick the active account: --account / SMTP_ACCOUNT > default_account > smtp{} block.

    Returns (normalized settings, selected account name, error). An explicitly
    requested account that does not exist is an error; a missing default falls
    back to the single smtp block.
    """
    accounts = file_data.get("accounts")
    accounts = accounts if isinstance(accounts, dict) else {}
    requested = getattr(args, "account", None) or os.environ.get("SMTP_ACCOUNT")
    if requested and requested not in accounts:
        return {}, requested, f"unknown account: {requested}"
    name = requested or file_data.get("default_account")
    if name and name in accounts and isinstance(accounts[name], dict):
        return _normalize(accounts[name]), name, None
    smtp = file_data.get("smtp")
    base = smtp if isinstance(smtp, dict) else file_data
    return _normalize(base), None, None


def _address_book_path() -> Path:
    """Locate the address book: SEND_EMAIL_ADDRESS_BOOK, else beside the secrets file."""
    override = os.environ.get("SEND_EMAIL_ADDRESS_BOOK")
    if override:
        return Path(override)
    workspace = os.environ.get("AAS_RUNTIME_WORKSPACE")
    if workspace:
        return Path(workspace) / ".address-book.json"
    secrets = _secrets_path()
    if secrets:
        return Path(secrets).parent / ".address-book.json"
    return Path(os.path.expanduser("~")) / ".send-email-address-book.json"


def _load_address_book() -> dict:
    """Return {address_lower: {address, name, last_used, times_used}}; robust to absence."""
    path = _address_book_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    contacts = data.get("contacts") if isinstance(data, dict) else None
    return contacts if isinstance(contacts, dict) else {}


def _save_address_book(contacts: dict) -> Path:
    path = _address_book_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"contacts": contacts}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _saveable_recipients(args: argparse.Namespace) -> list[tuple[str, str]]:
    """Addresses the user explicitly sent to (To/Cc/Bcc), with display names if given."""
    raw = (args.to or []) + (args.cc or []) + (getattr(args, "bcc", None) or [])
    out: list[tuple[str, str]] = []
    for item in raw:
        for name, addr in getaddresses([item]):
            addr = addr.strip()
            if addr:
                out.append((addr, name.strip()))
    return out


def _add_to_book(contacts: dict, entries: list[tuple[str, str]]) -> list[str]:
    """Insert/update contacts; return the addresses that were newly added."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    added: list[str] = []
    for addr, name in entries:
        key = addr.lower()
        existing = contacts.get(key)
        if existing is None:
            contacts[key] = {"address": addr, "name": name, "last_used": now, "times_used": 1}
            added.append(addr)
        else:
            existing["last_used"] = now
            existing["times_used"] = int(existing.get("times_used", 0)) + 1
            if name and not existing.get("name"):
                existing["name"] = name
    return added


def load_config(args: argparse.Namespace) -> SmtpConfig:
    file_data = _read_secrets_file()
    secrets, account_name, account_error = _select_account(args, file_data)

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

    pgp_sign = _coerce_bool(pick(None, "SMTP_PGP_SIGN", "pgp_sign"), False)
    pgp_key = pick(getattr(args, "pgp_key", None), "SMTP_PGP_KEY", "pgp_key")
    pgp_passphrase = pick(None, "SMTP_PGP_PASSPHRASE", "pgp_passphrase")
    gnupg_home = pick(getattr(args, "gnupg_home", None), "SMTP_GNUPG_HOME", "gnupg_home")

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
        pgp_sign=pgp_sign,
        pgp_key=str(pgp_key) if pgp_key is not None else None,
        pgp_passphrase=str(pgp_passphrase) if pgp_passphrase is not None else None,
        gnupg_home=str(gnupg_home) if gnupg_home is not None else None,
        account=account_name,
        account_error=account_error,
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


def _apply_headers(msg: EmailMessage, args: argparse.Namespace, cfg: SmtpConfig) -> None:
    """Set the addressing/identity headers on a message (the outer entity)."""
    base_sender = _no_newline(args.sender or cfg.sender, "from")
    if not base_sender:
        raise ValueError("no sender address: set --from or SMTP_FROM")
    sender = _no_newline(_apply_from_name(base_sender, cfg.from_name), "from")
    sender_addr = parseaddr(base_sender)[1]

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


def _build_content(args: argparse.Namespace, cfg: SmtpConfig) -> EmailMessage:
    """Build just the content entity (body/html/attachments) with no addressing headers.

    Kept separate so it can be signed as-is for PGP/MIME (the signature covers this
    part exactly, and the addressing headers live on the outer multipart/signed)."""
    msg = EmailMessage()
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


def build_message(args: argparse.Namespace, cfg: SmtpConfig) -> EmailMessage:
    """Compose an unsigned EmailMessage (content + addressing headers)."""
    msg = _build_content(args, cfg)
    _apply_headers(msg, args, cfg)
    return msg


def _flatten_crlf(part: EmailMessage) -> bytes:
    """Serialize a message/part with CRLF line endings (RFC 5322 / SMTP canonical)."""
    buf = io.BytesIO()
    BytesGenerator(buf, policy=email_policy.SMTP).flatten(part)
    return buf.getvalue()


def _should_sign(args: argparse.Namespace, cfg: SmtpConfig) -> bool:
    if getattr(args, "no_sign", False):
        return False
    return bool(getattr(args, "sign", False) or cfg.pgp_sign)


def _gpg_detach_sign(data: bytes, *, key: str | None, gnupg_home: str | None,
                     passphrase: str | None) -> str:
    """Return an ASCII-armored detached signature over data via the gpg CLI."""
    if shutil.which("gpg") is None:
        raise ValueError("gpg not found on PATH; install GnuPG to sign with --sign")
    cmd = ["gpg", "--batch", "--no-tty", "--yes", "--armor", "--digest-algo", PGP_DIGEST]
    if gnupg_home:
        cmd += ["--homedir", gnupg_home]
    if key:
        cmd += ["--local-user", key]
    if passphrase is not None:
        cmd += ["--pinentry-mode", "loopback", "--passphrase-fd", "0"]
    with tempfile.NamedTemporaryFile(delete=False) as handle:
        handle.write(data)
        tmp = handle.name
    try:
        cmd += ["--detach-sign", "--output", "-", tmp]
        proc = subprocess.run(
            cmd,
            input=(passphrase.encode() + b"\n") if passphrase is not None else None,
            capture_output=True,
            timeout=30,
        )
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        detail = err.splitlines()[-1] if err else "gpg signing failed"
        raise ValueError(f"gpg signing failed: {detail}")
    return proc.stdout.decode("ascii")


def _make_boundary(*blobs: bytes) -> str:
    """A multipart boundary guaranteed not to appear in the signed parts."""
    while True:
        boundary = "=_pgpmime_" + os.urandom(18).hex()
        marker = ("--" + boundary).encode()
        if not any(marker in blob for blob in blobs):
            return boundary


def build_signed_message(args: argparse.Namespace, cfg: SmtpConfig) -> tuple[EmailMessage, EmailMessage]:
    """Build an RFC 3156 PGP/MIME signed message. Returns (outer, content_part).

    The signature must cover the first part exactly as transmitted, so we assemble
    the multipart first, extract the canonical content bytes the verifier will read
    (excluding the CRLF that belongs to the boundary), and sign those.
    """
    content = _build_content(args, cfg)
    del content["MIME-Version"]  # belongs on the outer entity, not the signed part

    base_sender = parseaddr(_no_newline(args.sender or cfg.sender, "from") or "")[1]
    key = getattr(args, "pgp_key", None) or cfg.pgp_key or base_sender or None
    gnupg_home = getattr(args, "gnupg_home", None) or cfg.gnupg_home

    sig_part = EmailMessage(policy=email_policy.SMTP)
    sig_part["Content-Type"] = 'application/pgp-signature; name="signature.asc"'
    sig_part["Content-Description"] = "OpenPGP digital signature"
    sig_part["Content-Disposition"] = 'attachment; filename="signature.asc"'
    sig_part.set_payload("")  # placeholder until the content bytes are known

    boundary = _make_boundary(_flatten_crlf(content))
    outer = EmailMessage(policy=email_policy.SMTP)
    _apply_headers(outer, args, cfg)
    outer["MIME-Version"] = "1.0"
    outer.set_payload([content, sig_part])
    outer["Content-Type"] = (
        f'multipart/signed; micalg="{PGP_MICALG}"; '
        f'protocol="application/pgp-signature"; boundary="{boundary}"'
    )

    raw = _flatten_crlf(outer)
    marker = b"--" + boundary.encode() + b"\r\n"
    delim = b"\r\n--" + boundary.encode()
    start = raw.index(marker) + len(marker)
    content_bytes = raw[start:raw.index(delim, start)]
    sig = _gpg_detach_sign(content_bytes, key=key, gnupg_home=gnupg_home,
                           passphrase=cfg.pgp_passphrase)
    sig_part.set_payload(sig)
    return outer, content


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
    for secret in (cfg.password, cfg.pgp_passphrase):
        if secret:
            text = text.replace(secret, "***")
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


def _message_summary(headers_msg: EmailMessage, content_msg: EmailMessage,
                     recipients: list[str], signed: bool) -> dict:
    attachments = [
        att.get_filename()
        for att in content_msg.iter_attachments()
        if att.get_content_type() != "application/pgp-signature"
    ]
    return {
        "from": headers_msg["From"],
        "reply_to": headers_msg["Reply-To"],
        "recipients": recipients,
        "cc": headers_msg["Cc"],
        "subject": headers_msg["Subject"],
        "has_html": any(part.get_content_type() == "text/html" for part in content_msg.walk()),
        "attachments": attachments,
        "message_id": headers_msg["Message-ID"],
        "signed": signed,
    }


def cmd_send(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    if cfg.account_error:
        return _fail("send", "unknown_account", cfg.account_error)
    signed = _should_sign(args, cfg)
    try:
        if signed:
            send_msg, content_msg = build_signed_message(args, cfg)
            headers_msg = send_msg
        else:
            send_msg = build_message(args, cfg)
            headers_msg = content_msg = send_msg
        recipients = _envelope_recipients(args, cfg)
    except (ValueError, OSError) as exc:
        return _fail("send", "build_failed", _redact(str(exc), cfg))
    if not recipients:
        return _fail("send", "no_recipients", "no recipients: pass --to, --cc, or --bcc")

    saveable = _saveable_recipients(args)
    book = _load_address_book()
    new_recipients = _dedupe([addr for addr, _ in saveable if addr.lower() not in book])

    summary = {"ok": True, "command": "send",
               **_message_summary(headers_msg, content_msg, recipients, signed)}
    summary["new_recipients"] = new_recipients
    if args.dry_run:
        summary["dry_run"] = True
        summary["bytes"] = len(_flatten_crlf(send_msg))
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
        from_addr = parseaddr(headers_msg["From"])[1]
        if signed:
            # Send the exact signed bytes so the transmitted content matches what
            # was signed (send_message would re-serialize and could break the sig).
            server.sendmail(from_addr, recipients, _flatten_crlf(send_msg))
        else:
            server.send_message(send_msg, from_addr=from_addr, to_addrs=recipients)
    except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
        return _fail("send", "send_failed", _redact(str(exc), cfg))
    finally:
        try:
            server.quit()
        except (smtplib.SMTPException, OSError):
            pass
    if getattr(args, "save_recipients", False) and saveable:
        try:
            _add_to_book(book, saveable)
            _save_address_book(book)
            summary["saved_to_address_book"] = [addr for addr, _ in saveable]
        except OSError as exc:
            summary["address_book_error"] = str(exc)
    _emit(summary)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    if cfg.account_error:
        return _fail("verify", "unknown_account", cfg.account_error)
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
    if cfg.account_error:
        return _fail("show-config", "unknown_account", cfg.account_error)
    path = _secrets_path()
    _emit({
        "ok": True,
        "command": "show-config",
        "account": cfg.account,
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


def cmd_accounts(_args: argparse.Namespace) -> int:
    file_data = _read_secrets_file()
    names = _account_names(file_data)
    default = file_data.get("default_account")
    _emit({
        "ok": True,
        "command": "accounts",
        "accounts": names,
        "default_account": default if default in names else None,
        "single_smtp_fallback": isinstance(file_data.get("smtp"), dict),
    })
    return 0


def _contact_rows(contacts: dict) -> list[dict]:
    rows = [
        {"address": v.get("address", k), "name": v.get("name", ""),
         "times_used": v.get("times_used", 0), "last_used": v.get("last_used")}
        for k, v in contacts.items()
    ]
    return sorted(rows, key=lambda r: r["address"].lower())


def cmd_contacts(args: argparse.Namespace) -> int:
    contacts = _load_address_book()
    if args.add:
        try:
            added = _add_to_book(contacts, [(args.add.strip(), (args.name or "").strip())])
            _save_address_book(contacts)
        except OSError as exc:
            return _fail("contacts", "address_book_error", str(exc))
        _emit({"ok": True, "command": "contacts", "action": "add", "address": args.add,
               "added": bool(added)})
        return 0
    if args.remove:
        key = args.remove.strip().lower()
        removed = contacts.pop(key, None) is not None
        if removed:
            try:
                _save_address_book(contacts)
            except OSError as exc:
                return _fail("contacts", "address_book_error", str(exc))
        _emit({"ok": True, "command": "contacts", "action": "remove",
               "address": args.remove, "removed": removed})
        return 0
    rows = _contact_rows(contacts)
    if args.search:
        query = args.search.lower()
        rows = [r for r in rows if query in r["address"].lower() or query in (r["name"] or "").lower()]
    _emit({"ok": True, "command": "contacts", "action": "list",
           "count": len(rows), "contacts": rows,
           "path": str(_address_book_path())})
    return 0


def _selftest_namespace(**overrides: object) -> argparse.Namespace:
    base = {
        "host": None, "port": None, "user": None, "password": None, "security": None,
        "timeout": None, "sender": "<sender-address>", "to": ["<recipient>"], "cc": [],
        "bcc": [], "subject": "Self test", "body": None, "body_file": None, "html": None,
        "html_file": None, "attach": [], "reply_to": None, "dry_run": True,
        "from_name": None, "signature": None, "signature_file": None,
        "signature_html_file": None, "no_signature": False, "no_reply_to_self": False,
        "no_bcc_self": False, "allow_insecure_auth": False, "account": None,
        "save_recipients": False, "sign": False, "no_sign": False, "pgp_key": None,
        "gnupg_home": None,
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

    # 13. Named-account selection: explicit > default; unknown is an error.
    fd = {"accounts": {"work": {"host": "h1"}, "lab": {"host": "h2"}}, "default_account": "work"}
    sel_lab, name_lab, err_lab = _select_account(argparse.Namespace(account="lab"), fd)
    _, name_bad, err_bad = _select_account(argparse.Namespace(account="nope"), fd)
    saved_env = os.environ.pop("SMTP_ACCOUNT", None)
    try:
        sel_def, name_def, err_def = _select_account(argparse.Namespace(account=None), fd)
    finally:
        if saved_env is not None:
            os.environ["SMTP_ACCOUNT"] = saved_env
    record("account_selection",
           name_lab == "lab" and sel_lab.get("host") == "h2" and err_lab is None
           and name_def == "work" and sel_def.get("host") == "h1"
           and name_bad == "nope" and err_bad is not None)

    # 14. Address book: add, dedupe, and reload via the on-disk file.
    with tempfile.TemporaryDirectory() as tmp:
        saved_book = os.environ.get("SEND_EMAIL_ADDRESS_BOOK")
        os.environ["SEND_EMAIL_ADDRESS_BOOK"] = str(Path(tmp) / "ab.json")
        try:
            addr = "a" + "@" + "x.example"
            addr_caps = "A" + "@" + "X.example"  # same address, different case
            book = _load_address_book()
            first = _add_to_book(book, [(addr, "A")])
            again = _add_to_book(book, [(addr_caps, "")])
            _save_address_book(book)
            reloaded = _load_address_book()
            key = addr.lower()
            ab_ok = (first == [addr] and again == []
                     and key in reloaded and reloaded[key]["times_used"] == 2)
        finally:
            if saved_book is None:
                os.environ.pop("SEND_EMAIL_ADDRESS_BOOK", None)
            else:
                os.environ["SEND_EMAIL_ADDRESS_BOOK"] = saved_book
    record("address_book_add_dedupe_reload", ab_ok)

    # 15. Signing control logic + content/headers split (offline; does not call gpg).
    sign_logic = (
        _should_sign(_selftest_namespace(sign=True), SmtpConfig())
        and not _should_sign(_selftest_namespace(sign=True, no_sign=True), SmtpConfig())
        and _should_sign(_selftest_namespace(), SmtpConfig(pgp_sign=True))
        and not _should_sign(_selftest_namespace(), SmtpConfig())
    )
    content_only = _build_content(_selftest_namespace(body="hi"), SmtpConfig())
    full = build_message(_selftest_namespace(body="hi"), SmtpConfig())
    split_ok = (content_only["From"] is None and content_only["Subject"] is None
                and full["From"] is not None and full["Subject"] is not None)
    record("sign_logic_and_content_split", sign_logic and split_ok)

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
    parser.add_argument("--account", help="named SMTP account from the secrets file (or set SMTP_ACCOUNT)")
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
    send.add_argument("--save-recipients", dest="save_recipients", action="store_true",
                      help="after a successful send, save the recipients to the address book")
    send.add_argument("--sign", action="store_true",
                      help="PGP/MIME sign the message with gpg (or set pgp_sign in config)")
    send.add_argument("--no-sign", dest="no_sign", action="store_true",
                      help="do not sign even if the account has pgp_sign enabled")
    send.add_argument("--pgp-key", dest="pgp_key",
                      help="gpg signing key (key id/fingerprint/email); defaults to the sender address")
    send.add_argument("--gnupg-home", dest="gnupg_home", help="GnuPG home dir for the signing key")
    send.add_argument("--dry-run", action="store_true", help="compose and report without sending")
    send.set_defaults(func=cmd_send)

    verify = sub.add_parser("verify", help="connect and authenticate, sending nothing")
    _add_connection_args(verify)
    verify.set_defaults(func=cmd_verify)

    show = sub.add_parser("show-config", help="print the resolved config with the password redacted")
    _add_connection_args(show)
    show.set_defaults(func=cmd_show_config)

    accounts = sub.add_parser("accounts", help="list named SMTP accounts (names only, no secrets)")
    accounts.set_defaults(func=cmd_accounts)

    contacts = sub.add_parser("contacts", help="address book: list (default), --add, --remove, --search")
    contacts.add_argument("--add", help="address to add/update (use with --name)")
    contacts.add_argument("--name", help="display name for --add")
    contacts.add_argument("--remove", help="address to remove")
    contacts.add_argument("--search", help="filter the listing by address or name")
    contacts.set_defaults(func=cmd_contacts)

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
