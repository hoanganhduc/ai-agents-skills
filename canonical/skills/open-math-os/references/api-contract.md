# Open Math OS Client Contract

This file describes what `open_math_os.py` sends, what it accepts, and how it fails. The API facts were checked with live calls on 2026-11-05, against OpenAPI v0.51.4. On 2026-11-07 the spec reported v0.52.0, with the same content apart from the version.

## Endpoint and transport

- The origin is fixed at `https://os.openmathmodel.org`, with the API under `/v1`. No flag or environment variable changes it. Only an internal test argument can point the client at a loopback server.
- The client uses `POST /v1/chat/completions` for answers, `GET /v1/models` for the catalog, and `GET /v1/openapi.json` (no key) in `doctor --live`.
- `doctor --live` checks that the spec lists both paths and names `supported_reasoning_efforts`, and that every catalog entry declares its efforts. The spec documents a chat body as `ModelCall`, which names only `model` and keeps the fields OpenAI's chat completion API defines. So `reasoning_effort` and `max_completion_tokens` are not named in the spec and are not checked against it.
- No proxy is used, and no redirect is followed: a 3xx response is refused as a client bug. Plain HTTP is refused.
- `Authorization: Bearer <model key>` is attached as an unredirected header. The `User-Agent` is `ai-agents-skills-open-math-os/<version>`, because Cloudflare answers the default `Python-urllib` agent with HTTP 403 "error code: 1010".

## Request body

- `model` must match `vendor/name` (ASCII letters, digits, `.`, `_`, `-`) and appear in the catalog. The catalog is cached for 86,400 seconds.
- `reasoning_effort` must be in the model's `supported_reasoning_efforts`. The gateway drops fields that OpenAI chat completions does not define, so effort travels only in this field.
- `max_completion_tokens` is always sent. The default depends on the effort (see `models.md`); `--max-tokens` may set 1 to 32,768.
- With streaming (the default), the body also sets `stream_options.include_usage`, so the last chunk carries `usage`.

## Responses

- The answer is `choices[0].message.content` (or the streamed `delta.content`), with `reasoning` and `reasoning_details` beside it. `usage` reports prompt, completion, reasoning and total tokens. There is no cost field and no rate-limit header. The `x-request-id` header holds the request id.
- The stream is SSE and ends with `data: [DONE]`. The idle timeout is 120 seconds. The total deadline defaults to 1,800 seconds and `--deadline` changes it. A stream that ends without `[DONE]` is incomplete.
- The deadline covers the whole response read, streamed or not. When it passes, a watchdog shuts the connection down, so a peer that trickles bytes inside one line cannot hold the client past it.
- `content` and `reasoning` are returned with ANSI CSI and OSC sequences and C0/C1 control characters removed (newline and tab are kept), including while they stream to a terminal. `reasoning_details` is kept verbatim inside the JSON envelope, where JSON escaping makes it inert.

## Output envelope

`ask --json` prints one JSON object on one line, with these keys in this order: `schema` (`open-math-os.ask.v1`), `status` (`ok`, `truncated`, `incomplete`, `error` or `blocked`), `label`, `requested_effort`, `max_completion_tokens`, `finish_reason`, `usage`, `error`, `provenance`, `output` and `final`. The last key is always `"final": true`, so a reader can tell a complete envelope from a cut one.

`output` holds the remote content under `trust: "untrusted-remote-output"`. `provenance` holds `provider_id`, `provenance_label`, `family`, `independence_eligible`, `requested_model`, `served_model_claimed`, `request_id`, `captured_at` and `catalog_fetched_at`.

Without `--json`, the answer is printed between `[open-math-os <nonce>] begin` and `[open-math-os <nonce>] end` lines, followed by a provenance line. The nonce is random for each answer, so the answer text cannot fake the end marker.

## Errors and exit codes

Every failure prints one line on stderr: `HTTP <status> <class>: <code>` for an HTTP answer, `HTTP 000 <class>: <code>` for a transport failure, or `refused <class>: <code>` for a local refusal. The code is a short token from the error envelope; the remote error message is never printed.

| Class | Cause | Exit |
|---|---|---|
| `usage` | invalid flag, model id, effort or input file; model not in the catalog | 2 |
| `quota` | HTTP 429, or the circuit breaker is open | 5 |
| `edge_block` | HTTP 403 with a Cloudflare "error code" body (1010 is the User-Agent block) | 6 |
| `auth` | HTTP 401, any other 403, a missing or malformed key, or a platform key | 7 |
| `bad_request` | HTTP 400, 404, 422 and other 4xx | 11 |
| `client_bug` | HTTP 405, a refused redirect, a malformed answer | 12 |
| `transient` | HTTP 5xx and 408, transport errors, timeouts, a busy limiter | 13 |
| `budget` | a run, day or week cap, or the quota-snapshot floor | 14 |
| `blocked` | the outbound screen matched, or an input file is in a credential location or protected | 15 |
| `state` | the local state or key file is unsafe or unusable | 16 |
| `hard_quota` | HTTP 402, or the hard-quota marker is present | 18 |

Exit codes 5, 7 and 18 follow `canonical/instructions/provider-credit-quota.md`. A `truncated` answer exits 0.

## Retries and the circuit breaker

- A retry happens only when no response byte has arrived: a connection error, a 429, or a 5xx before the stream starts. A stream that fails after its first byte is reported as `incomplete` and not retried.
- A `Retry-After` of at most 300 seconds is honored. A longer one stops the retries. Without the header, the wait is full-jitter exponential backoff with a 20-second base and a 120-second cap. There are at most 2 retries.
- Labels that start with `agd:` or `arl:` never retry. Neither does a launch inside an ARL run, where `AAS_AUTOLOOP_RUN_ID` or `AAS_ARL_BROKER_SOCKET` is set. The loader drops those markers, so `run_skill.sh` and `run_skill.ps1` pass their presence to the client as `AAS_OPENMATH_ARL_LAUNCH=1`.
- Three 429 responses within 120 seconds open a machine-wide breaker. While it is open, no process sends for 600 seconds.
- An HTTP 402 writes `hard_quota.json`. Every process refuses to send until the user deletes the file.

## Limiter and budgets

- At most 4 requests are in flight on the machine. Each holds one slot file under an operating-system lock (`flock`, or `msvcrt` on Windows) that records its PID. The lock dies with its process. A slot found free with an old owner record is reported as a recovered stale slot.
- A run is named by the first two colon-separated fields of the label. A one-field label names no run, so that invocation is its own run. Each run may make at most 50 requests and use at most 250,000 tokens.
- Across all runs, the client allows 1,000,000 tokens per rolling day and 4,000,000 per rolling week.
- Each attempt reserves `max_completion_tokens` plus a prompt estimate (bytes / 4). Once the attempt holds a limiter slot, the client checks the hard-quota marker, the breaker and every cap again under the state lock. In the same step it writes the reservation to `budget.jsonl` and a provisional `inflight` record to `sends.jsonl`, so concurrent processes see each other's reservations.
- After the attempt, a settlement row replaces the reservation by `usage.total_tokens`. When usage is missing, the attempt is charged the reservation if any byte arrived, and nothing otherwise. An attempt killed in flight has no settlement, so it stays charged at its reservation.
- When `quota.json` in the state directory is at most 900 seconds old, a send also needs at least 50 USD left in both team windows. The expected shape is `{"fetched_at": "<UTC Z time>", "windows": {"five_hour": {"remaining_usd": N}, "week": {"remaining_usd": N}}}`. A fresh file with another shape refuses the send.

## Local state

The state directory is `~/.local/state/ai-agents-skills/open-math-os` (mode `0700`), derived from `HOME`. On Windows it is `%LOCALAPPDATA%\ai-agents-skills\state\open-math-os`, checked by the broker's owner and DACL guard. Files are `0600`, opened without following links:

- `sends.jsonl`: the metadata-only send log, rotated at 1 MiB with 3 old files kept. Each attempt writes an `inflight` record before it sends and a final record after it; both carry the same `attempt_id`.
- `budget.jsonl`: the durable budget ledger. It is never rotated; only rows older than the weekly window are pruned. A settlement row carries the timestamp of its reservation, so the two always leave the window together.
- `catalog.json`, `breaker.json`, `hard_quota.json`, `quota.json`, `state.lock` and `limiter-slot-<n>.lock`.

## Outbound screen

The screen covers the system prompt, the prompt, the prompt file and any conversation history. It matches:

- the credential patterns and the credential-named environment values of the ARL panel screen, copied with its personal-data patterns from `panel_parent.py` and kept in sync by a test;
- boundary-anchored patterns for Open Math keys (`nuc_ak_`, `nuc_oc_`), `sk-` keys, GitHub fine-grained tokens, `Authorization: Basic|Bearer` headers, PGP private-key blocks, Google API keys, Hugging Face tokens and JWTs, and `Bearer` followed by a token of 20 or more characters (`bearer-token`), which also finds a header kept as separate name and value fields, as in a HAR file;
- an extended credential assignment (`credential-assignment-extended`): a credential-like name in any letter case (`api_key`, `API key`, a name ending in `_KEY` or `-KEY`, `auth`, `authorization`, `bearer`, `cookie`, `credential`, `credentials`, `oauth`, `pass`, `password`, `passwd`, `passphrase`, `private_key`, `secret`, `session` or `token`), optionally numbered (`_2`); then optionally a closing quote, backtick or backslash-escaped quote, a `]` and Markdown bold; `=`, `:` or `=>`; optionally an opening quote, backtick or escaped quote and a `Basic`, `Bearer` or `Token` scheme word; and a value that starts with 8 characters from `A-Za-z0-9_./+@:-`. It finds JSON entries such as `"ZOTERO_API_KEY": "..."`, Python dict reprs, `"name" : value`, JSON strings and notebook cells such as `api_key=\"...\"`, subscripts such as `os.environ["HCLOUD_TOKEN"] = "..."`, Markdown such as `` `ZOTERO_API_KEY`: `...` `` and `**Password:** ...`, PHP and Ruby `=>`, `"Authorization": "Bearer ..."`, `Authorization: Token ...`, and lines such as `KAGGLE_KEY=...`, `SMTP_PGP_PASSPHRASE=...` and `HCLOUD_TOKEN_2=...`;
- an environment call (`credential-env-call`): `setenv`, `putenv` or `setdefault` with a quoted credential-like name and a quoted value that starts with 8 such characters, as in `os.environ.setdefault("HCLOUD_TOKEN", "...")` and `monkeypatch.setenv(...)`;
- a command-line flag (`credential-flag`): `--api-key`, `--key`, `--password`, `--passwd`, `--passphrase`, `--secret` or `--token`, also after a prefix such as `--client-secret`, then a blank and such a value;
- a `.netrc` entry (`netrc-credential`): `machine <host>` and up to three `login`, `account` or `port` tokens, or `default` and one to three of them, in any order, then `password <secret>`, on one line or across lines. So a `machine` entry without a login, or with the password before the login, is found too. A `default` entry needs one of those tokens before `password`: `default password <secret>` is not found, also with a `login` after it;
- the exact value of every loaded `OPENMATH_*` variable, in the raw text and in a canonical copy of all messages joined (NFKC, without format characters or whitespace, case-folded). A key that is hard-wrapped, split by a zero-width space, upper-cased, or split exactly where one message ends and the next begins, with nothing between the two parts, is still found;
- email addresses, by the panel's email pattern and by the exact values of email-bearing variables (`*EMAIL*`, `SMTP_FROM`, `SMTP_USER`, `SMTP_REPLY_TO`, `SMTP_CC`, `SMTP_BCC`), in the raw text and in a normalized copy of each message.

The normalized email copy undoes the following forms, and no others. It applies NFKC, so a fullwidth at sign (U+FF20) becomes `@`. It drops format characters, such as the zero-width space, and control characters other than tab, line feed and carriage return. It decodes `%40`, `&#64;`, `&#x40;`, `&commat;` and `\u0040`, in any letter case and with leading zeros in the numeric entities. It turns `at` between brackets into `@` and `dot` between brackets into `.`, with blanks inside the brackets and the blanks and line breaks around them. Each bracket may be square, round or curly and the two need not match, so `[at]`, `(at)`, `{at}`, `[dot]`, `(dot)`, `{dot}` and `[at)` are all undone. It treats a `}` or `}$` before `@` as part of the local part, so a grouped `{a,b}@d` (LNCS `\email{\{a,b\}@d}`, or `$\{$a, b$\}$@d` in LaTeX source) is found, also with a line break on either side of `@`. It joins an address that has blanks or one line break around `@`, or line breaks after dots in its domain, as PDF copy and hard-wrapped text produce (`local @ domain`, `local@ domain`, `@` at the start or end of a line, `\url` broken after a dot), when two local-part characters come before `@`, a blank or line break before `@` has one after it too, every label of the domain is ASCII, and the domain ends in a top-level label of two to four lowercase letters that no letter, digit, `_`, `(`, `[` or `.label` follows. It removes a line break directly after `@`. It splits off a footnote digit glued after such a top-level label only in an address written whole, with no blank or line break in it; a spaced or broken address followed by a footnote digit is not joined. For the pattern check only, a non-ASCII letter or digit glued before the local part or after the domain is split off.

Code and markup such as `l@{}r`, `@[simp]`, `exact @Nat.rec` (also with a line break before `@`), decorators and the matrix products `x @ self.weight.T`, `hidden @ self.weight`, `evecs @ np.diag(w)` and `Ainv @ rhs.ravel()` are still sent. A matrix product whose left operand has two or more characters and whose right operand is a dotted name ending in two to four lowercase letters with nothing after it, such as `w.data`, reads as an address and is blocked.

The email pattern is tried once per `@`, at the first word boundary of the run of local-part characters before it. This finds exactly the matches of a full search, in linear time, so a long unspaced expression such as `x0+x1+...+xn` cannot stall the screen.

The loader of a managed launch keeps no email-bearing variable. `run_skill.sh` and `run_skill.ps1` therefore capture those values before their scrub and pass them to this command only, as `NAME=value` lines in `AAS_OPENMATH_SCREEN_VALUES`. A direct wrapper launch adds the values in its own environment. A value without `@` is skipped.

The panel's other personal-data categories (phone, government id, person record, labeled personal data) do not block. Prompts are sent freely apart from credentials and email addresses, and digit groups in mathematics can look like phone numbers. A match prints one `note:` line on stderr, and the send-log records carry the category labels in `pii_reported`.

## Input files

The paths of `--prompt-file` and `--system-file` are checked before anything is opened. The send is refused with `blocked: input_file_is_managed_secret` (exit 15) when the path as given, a link on the way to the file, or the fully resolved path is one of these locations or lies inside one: `~/.config/ai-agents-skills/`, `~/.claude/secrets.json`, `~/.claude/.credentials.json`, `~/.codex/auth.json`, `~/.openclaw/secrets.json`, `~/.openclaw/openclaw.json`, `~/.openclaw/credentials/`, `~/.ssh/`, `~/.gnupg/`, `~/.aws/`, `~/.docker/`, `~/.kaggle/` and `~/.netrc`. Here `~` is `HOME`, and each location is compared both as written and with its own links resolved. So an absolute or relative link into a store, a path through a linked directory into one, and a store file that is itself a link out of its store, as a dotfile manager makes it, are refused.

The file is then opened with its links followed, as `/dev/stdin` and `/dev/fd/N` need. Where the kernel reports the path of the opened file (`/proc/self/fd` on Linux), that path is checked against the same locations and against `/proc/` and `/sys/` before anything is read, so a link swapped in after the first check is refused too. The file is also refused when its `st_dev:st_ino` identity is listed in `AAS_PROTECTED_SECRET_FILE_IDS`. The managed loader sets that variable for the secret files it projected, so a hard link to one of them is refused as well. Like a screen block, each of these refusals writes a metadata-only `blocked` record to `sends.jsonl`. A file that is not regular, such as a pipe from `cat file |` or a short bash here-document, is refused as `usage: input_file_not_regular` (exit 2), without a blocked record.

A link to an ordinary file, an owner-private (`0600`) prompt file, a path under a linked ancestor directory, such as `/tmp` on macOS, and a here-document that the shell passes to `/dev/stdin` as an unlinked file, as zsh does, are read as before.

## Limits of the screen

The screen is defense in depth against accidental disclosure, not protection against deliberate exfiltration. It does not detect:

- homoglyphs, base64 or other encodings, and reversed or spelled-out addresses;
- values split across messages, such as a local part in the system prompt and the domain in the prompt. Only a loaded `OPENMATH_*` value is also matched where one message ends and the next begins, and only when nothing comes between its two parts. A key hard-wrapped behind a line prefix such as `> ` or `+`, or with a line-continuation backslash, is not matched either;
- secrets with no credential-like name (a bare list of values, a token in a URL path), a bare `key` name (a JSON `"key"` field, a `key =` line, a `?key=` URL parameter), and a credential-named value that does not start with 8 characters from `A-Za-z0-9_./+@:-`, such as a password with symbols or spaces;
- credentials in other layouts: XML elements such as `<password>`, separate name and value fields other than a `Bearer` token (Kubernetes `env` entries, .NET `<add key=... value=...>`), tables, CSV and tuples, cookie jars, names with more after the credential word (`secret_key_base`), positional arguments such as `auth=("user", "...")`, and short or nameless forms such as `docker login -p` and `curl -u user:...`;
- other ordinary forms found by the M1 review: typed declarations (`api_key: str = "..."`, `val apiKey: String = "..."`, Lean `def apiKey : String := "..."`), the operators `:=`, `?=`, `+=`, `??=` and `<-`, string prefixes and triple quotes (`r"..."`, `b"..."`, `f"..."`, C# `@"..."`, `"""..."""`), smart quotes, a fullwidth colon, a no-break space in the name, command forms (`aws configure set`, `git config`, Dockerfile `ENV`, `set -gx`, `setx`, `npm config set ..._authToken`, `curl --user`), call arguments (`os.getenv("API_KEY", "...")`, `keyring.set_password(...)`, an argparse `default=`), user info in non-HTTP URLs (`postgresql://user:...@host`, `mysql://`, `redis://`, `amqp://`, `sftp://`), camelCase or infixed names (`secretKey`, `accessKey`, `secretAccessKey`, `AccountKey=`, `PWD=`, `Secret key:`), a name with a three-digit suffix (`TOKEN_123`), a dotted name (`api.key=`), a numbered name in an environment call (`setenv("API_KEY_2", "...")`), and a `.netrc` `default` entry with no token before `password`;
- email addresses with a blank or line break before `@` and none after it (`local @domain`), which code also writes (Lean's `exact @Nat.rec`, decorators); a spaced or broken address whose local part does not end in two ASCII local-part characters, whose top-level domain is longer than four letters or upper-case, or whose domain breaks after a hyphen; an address whose domain starts with a non-ASCII label, found only through a carried value, and then only when it is written whole or with a single line break directly after `@` (a blank around `@`, a line break before it or a break inside the domain is not undone for a non-ASCII domain); a spaced or broken address followed by a footnote digit; and less common forms such as `<at>`, `[.]`, `(a, b)@d`, double-encoded entities, quoted-printable `=40`, HTML tags or comments around `@`, `(cid:64)` from some PDF extractors and the ideographic full stop used as a dot;
- a hard link, made elsewhere, to a file in one of the locations above, unless the loader lists that file's identity; a link swapped in between the check and the open where the kernel does not report the opened file's path (macOS, Windows); and, on a case-insensitive file system such as the macOS default, a store path written with different letter case.
