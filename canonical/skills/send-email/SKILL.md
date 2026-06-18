---
name: send-email
description: Send email over SMTP using only the Python standard library, with plain-text and HTML bodies, file attachments, cc/bcc, reply-to, a dry-run preview, connection verification, and redacted config inspection.
user-invocable: true
disable-model-invocation: false
metadata:
  short-description: Send email via SMTP with text/HTML, attachments, and cc/bcc
  requires:
    bins:
      - python3
---

# Send Email

Send email over SMTP from any install target on any operating system. The runtime
is pure Python standard library (`smtplib`, `ssl`, `email`), so there is nothing
to install beyond Python 3.10+. It composes plain-text and HTML messages with
attachments, cc/bcc, and reply-to, previews a message with `--dry-run`, checks a
server with `verify`, and exposes the resolved settings (password redacted) with
`show-config`.

Use this skill when the user wants to send a message, mail a file, deliver a
report or notification by email, or test SMTP credentials. For document delivery
over Telegram use `vnu-eoffice`; this skill is email/SMTP only.

Credentials are never hardcoded, printed, or committed: they are read from
environment variables or a JSON secrets file, and redacted out of any error.

## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command
target. For Codex-only installs the runtime is usually
`%USERPROFILE%\.codex\runtime`; for multi-agent installs it is usually
`%LOCALAPPDATA%\ai-agents-skills\runtime`. Set `$runtime` to the installed runtime
root, then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/send-email/run_send_email.bat" <args>
& "$runtime\run_skill.bat" "skills/send-email/run_send_email.ps1" <args>
```

POSIX examples below use `run_skill.sh` and the `.sh` command target; use the
Windows command target above on native Windows.

## Configuration

Settings resolve in increasing precedence: secrets file, then environment
variables, then explicit command-line flags.

- Connection environment variables: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
  `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_SECURITY` (`ssl` | `starttls` | `plain`),
  `SMTP_TIMEOUT`.
- Pre-defined sender identity (all optional): `SMTP_FROM_NAME`, `SMTP_REPLY_TO`,
  `SMTP_CC`, `SMTP_BCC` (comma-separated), `SMTP_SIGNATURE`, `SMTP_SIGNATURE_HTML`,
  `SMTP_REPLY_TO_SELF`, `SMTP_BCC_SELF`.
- Secrets file: the managed runner sets `AAS_SECRETS_FILE` to
  `workspace/.secrets.json`. Put an `smtp` object there (or top-level `SMTP_*`
  keys) holding both the connection settings and the identity defaults:

```json
{
  "smtp": {
    "host": "smtp.example.com",
    "port": 587,
    "security": "starttls",
    "user": "<smtp-username>",
    "password": "<app-password>",
    "from": "<sender-address>",
    "from_name": "Your Name",
    "reply_to": "<reply-address>",
    "cc": ["<standing-cc>"],
    "bcc": ["<standing-bcc>"],
    "signature": "--\nYour Name\nYour Lab",
    "signature_html": "<p>Your Name<br>Your Lab</p>",
    "reply_to_self": true,
    "bcc_self": true
  }
}
```

The identity fields are optional and not secret, but they live in the same
`smtp` object for one-file configuration; only `user`/`password` are sensitive.
`from_name` is combined with `from` to send as `Your Name <addr>` (or embed the
name directly in `from`). The `signature` (and optional `signature_html`) is
appended after the standard `-- ` delimiter; if only a text signature is set it is
also wrapped into the HTML alternative.

By default **Reply-To and Bcc are set to the sender address** (so replies come
back to you and you keep a copy). Set `reply_to_self`/`bcc_self` to `false`, or
pass `--no-reply-to-self` / `--no-bcc-self`, to disable. An explicit `reply_to`
(or `--reply-to`) overrides the self-default; `--cc`/`--bcc` add to the standing
lists.

If `--port`/`--security` are omitted, port 465 implies `ssl`, port 25 implies
`plain`, and the default is `starttls` on port 587. Common hosts: `smtp.gmail.com`
and `smtp.office365.com` (use an app password, not the account password).

Never write a real address, password, or token into a tracked file; pass them via
the environment or the secrets file. Use `show-config` to confirm what resolved.

## Commands

Run via the managed runner (POSIX shown; see Windows Runtime Commands above):

```bash
bash ~/.local/share/ai-agents-skills/runtime/run_skill.sh skills/send-email/run_send_email.sh <command> [args...]
```

- `send` -- compose and send. Recipients (`--to`, `--cc`, `--bcc`) are repeatable
  and may be comma-separated. Body is `--body`/`--body-file` and/or
  `--html`/`--html-file` (both present become a multipart/alternative). Attach
  files with repeated `--attach`. Identity overrides: `--from-name`, `--reply-to`,
  `--signature`/`--signature-file`/`--signature-html-file`, `--no-signature`,
  `--no-reply-to-self`, `--no-bcc-self`.
- `--dry-run` -- with `send`, compose and report the message (from, reply-to,
  recipients, cc, subject, html flag, attachments, byte size) without connecting
  or sending.
- `verify` -- connect and authenticate to the server, then disconnect; sends no
  message. Use it to test credentials.
- `show-config` -- print the resolved host/port/security/user/from/timeout and
  whether a password is set; the password itself is never printed.
- `selftest` -- offline smoke (no network): builds, serializes, and re-parses
  messages in memory to validate message construction.

Examples:

```bash
# preview without sending
bash ~/.local/share/ai-agents-skills/runtime/run_skill.sh skills/send-email/run_send_email.sh \
  send --to <recipient> --subject "Report" --body "See attached." --attach ~/report.pdf --dry-run

# send a text + HTML message to several recipients
bash ~/.local/share/ai-agents-skills/runtime/run_skill.sh skills/send-email/run_send_email.sh \
  send --to <recipient> --cc <reviewer> --subject "Update" \
  --body "Plain fallback." --html "<p>Rich <b>body</b>.</p>"

# test the server and credentials
bash ~/.local/share/ai-agents-skills/runtime/run_skill.sh skills/send-email/run_send_email.sh verify
```

Every command prints a single JSON object. On success it includes `"ok": true`;
on failure it includes `"ok": false` with an `error_code` and a redacted message,
and the process exits non-zero.

## Natural-language routing

- "email this file to <recipient>": run `send` with `--attach`; preview with
  `--dry-run` first if the recipient or content is unconfirmed.
- "does my SMTP setup work?": run `verify`.
- "what mail settings are configured?": run `show-config`.

## Security notes

- TLS certificates are validated (`ssl.create_default_context`); prefer `ssl` or
  `starttls` over `plain`.
- Authenticating over an unencrypted (`plain`) connection is refused unless you
  pass `--allow-insecure-auth`, so credentials are not sent in the clear by
  accident (note port 25 with no `--security` resolves to `plain`).
- Header values are rejected if they contain newlines, preventing header
  injection.
- Passwords are read only from the environment or the secrets file and are
  redacted from all output and errors.
