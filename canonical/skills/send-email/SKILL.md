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
target. Set `$runtime` to the installed runtime root. Multi-agent installs
usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
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
A ready-to-edit sample ships at `skills/send-email/send-email.example.json`
(installed to `<runtime_root>/workspace/skills/send-email/send-email.example.json`).
Copy its `smtp` (or `accounts`) block into your secrets file and replace the
`<placeholders>`.

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

Multiple accounts: instead of (or alongside) a single `smtp` block, define an
`accounts` map of named profiles and an optional `default_account`; each profile
holds the same keys (connection + identity). Select one with `--account NAME` (or
`SMTP_ACCOUNT`); `accounts` lists the names.

```json
{ "default_account": "work",
  "accounts": {
    "work": {"host": "smtp.work.example", "user": "<u>", "password": "<p>", "from": "<work-from-address>", "from_name": "You (Work)", "pgp_sign": true, "pgp_key": "<gpg-key-id-or-email>"},
    "lab":  {"host": "smtp.gmail.com", "port": 587, "user": "<u>", "password": "<p>", "from": "<lab-from-address>"}
  } }
```

If `--port`/`--security` are omitted, port 465 implies `ssl`, port 25 implies
`plain`, and the default is `starttls` on port 587. Common hosts: `smtp.gmail.com`
and `smtp.office365.com` (use an app password, not the account password).

### Where to put the config across install targets

Each install target reads its own `<runtime_root>/workspace/.secrets.json`, and
the runtime root differs per target:

- Codex: `$AAS_RUNTIME_WORKSPACE/.secrets.json` (Windows: `%AAS_RUNTIME_WORKSPACE%\.secrets.json`)
- multi-agent installs (claude/deepseek/opencode/antigravity): `~/.local/share/ai-agents-skills/runtime/workspace/.secrets.json` (Windows: `%LOCALAPPDATA%\ai-agents-skills\runtime\workspace\.secrets.json`)

To make one configuration serve **all** targets, use either approach (both work
on every OS, and CLI flags still override):

1. One shared secrets file: set `AAS_ALLOW_EXTERNAL_SECRETS_FILE=1` and
   `AAS_SECRETS_FILE=<one path>` (e.g. `~/.config/send-email/secrets.json`) in
   your shell profile; every target's runner then reads that single file.
2. Environment variables: put `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`,
   `SMTP_FROM`, `SMTP_FROM_NAME`, `SMTP_ACCOUNT`, etc. in a shared profile (e.g.
   `~/.secrets.env`); the skill reads env before the secrets file, so all targets
   pick them up. Otherwise, drop the same `.secrets.json` into each target's
   `workspace/` directory listed above.

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
- `show-config` -- print the resolved account/host/port/security/user/from/identity
  and whether a password is set; the password itself is never printed.
- `accounts` -- list configured named SMTP accounts (names only, no secrets).
- `contacts` -- the address book: lists saved contacts by default; `--add ADDR
  [--name N]`, `--remove ADDR`, `--search QUERY`.
- `selftest` -- offline smoke (no network): builds, serializes, and re-parses
  messages in memory to validate message construction.

Account and identity selection: `--account NAME` (or `SMTP_ACCOUNT`) picks a named
account; otherwise `default_account`, otherwise the single `smtp` block. Per-send
CLI flags override the chosen account.

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

## Address book (save recipients for reuse)

`send` reports `new_recipients` -- the addresses in this message that are not yet
in the address book. **After a successful send, if `new_recipients` is non-empty,
ask the user whether to save those addresses for later; if they agree, run
`contacts --add <address> [--name <name>]` for each** (or send with
`--save-recipients` to save them automatically). The book is a JSON file at
`<runtime_workspace>/.address-book.json` (override with `SEND_EMAIL_ADDRESS_BOOK`);
it is personal state and should be backed up alongside the secrets file. Use
`contacts --search` to look up a saved address before composing.

## PGP signing (optional)

Messages can be PGP/MIME signed (RFC 3156: `multipart/signed` with a detached
signature, which works with HTML and attachments). This needs **GnuPG (`gpg`)** on
PATH with your secret key in the keyring; it adds no Python dependency, and
unsigned sending still works if `gpg` is absent.

- Enable per send with `--sign`, or per account with `"pgp_sign": true`.
- The signing key defaults to the sender address; override with `--pgp-key <id>`
  or `"pgp_key"`. Use `--gnupg-home`/`"gnupg_home"` for a non-default keyring, and
  `"pgp_passphrase"` in the secrets file only if the key needs a passphrase and no
  `gpg-agent` is available (otherwise rely on the agent). `--no-sign` overrides a
  per-account default.
- `send` reports `"signed": true/false`. Example:

```bash
… run_send_email.sh send --account work --to <recipient> --subject Hi --body Hi --sign
```

The passphrase is passed to gpg over a pipe (never on the command line) and is
never printed.

For a passphrase-protected key, prefer `"pgp_passphrase"` in the secrets file for
**non-interactive/automated** signing: the skill then uses gpg loopback and needs
no agent or terminal, so it works headlessly (this is the reliable path for an
agent sending mail). Relying on a cached `gpg-agent` instead is fragile here: a
non-interactive run has no TTY, so on a cache miss gpg fails with
`Inappropriate ioctl for device` (it cannot show a pinentry prompt). The agent
cache also only populates from an interactive terminal that has `export
GPG_TTY=$(tty)` set, and it expires after gpg-agent's cache TTL (default ~10
minutes) — so treat gpg-agent as a convenience for your own terminal, not for
unattended signing.

## Natural-language routing

- "email this file to <recipient>": run `send` with `--attach`; preview with
  `--dry-run` first if the recipient or content is unconfirmed. After sending,
  offer to save any `new_recipients` to the address book.
- "sign this email" / "send signed": add `--sign` (needs gpg + your key).
- "send from my work account": run `send --account work` (see `accounts`).
- "who do I have saved?" / "look up <name>'s email": run `contacts` /
  `contacts --search <query>`.
- "does my SMTP setup work?": run `verify` (add `--account` to test a specific one).
- "what mail settings are configured?": run `show-config` / `accounts`.

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
