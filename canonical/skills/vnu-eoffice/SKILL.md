---
name: vnu-eoffice
description: "Route VNU eOffice requests to an existing vnu_eoffice package or CLI: monitor updates, list latest incoming/outgoing documents, search by keyword, download attachments, and send requested files through Telegram."
user-invocable: true
disable-model-invocation: false
metadata: {"requires":{"bins":["python3"]}}
---

Use this skill when the user asks about VNU eOffice, VNU e-office, eoffice.vnu.edu.vn, incoming documents, outgoing documents, document summaries, document searches, or Telegram delivery of eOffice attachments.

Core rules:
- Do not fork or paste target-specific helper code into this canonical skill.
- Do not print credentials, tokens, chat ids, or secret setup instructions.
- Use both modules by default: `den` for incoming and `di` for outgoing.
- Ignore eOffice read/unread state. The user may also read posts manually in a browser, so selection and monitoring must rely on fetched document ids.
- Fetch multiple pages by default. Use `--pages N` when the user asks for a deeper or shallower scan.
- Only download and send document files when the user explicitly asks for files or asks to download/send results.
- After sending files, delete local copies unless the user explicitly asks to keep them.
- Preserve the package's numbered latest/search/monitor output. The package persists item numbers for follow-up download/send requests.
- Use `items` before resolving an ambiguous follow-up request when the current item numbers are not visible in the conversation.

Execution surface:
- Prefer the installed package CLI: `python3 -m vnu_eoffice <command>` or `vnu-eoffice <command>`.
- This skill requires an importable `vnu_eoffice` package/checkout or `vnu-eoffice` executable. If neither is available, report the missing dependency instead of claiming eOffice access.
- If the package is not importable, use the target's normal project checkout mechanism to make `vnu_eoffice` importable. Do not hardcode a user-specific checkout path into the skill.
- Target adapters may wrap the package CLI with convenience command names such as `latest`, but numbered items are package behavior. Keep adapter-only logic outside this canonical skill body.
- Secrets and Telegram config must come from the existing runtime environment or local secret store. This skill intentionally does not provide secret setup instructions.

Common commands:
- `python3 -m vnu_eoffice test-login`
- `python3 -m vnu_eoffice monitor --no-notify --limit 60 --pages 2`
- `python3 -m vnu_eoffice list --limit 10 --pages 2 --modules den,di`
- `python3 -m vnu_eoffice search "<keywords>" --limit 10 --pages 2 --modules den,di`
- `python3 -m vnu_eoffice search "<keywords>" --limit 5 --pages 2 --has-attach`
- `python3 -m vnu_eoffice items`
- `python3 -m vnu_eoffice download --item 5`
- `python3 -m vnu_eoffice send --item 2,4 --delete-after`
- `python3 -m vnu_eoffice download --id den:12345`
- `python3 -m vnu_eoffice send --id di:98765 --delete-after`

Natural-language routing:
- "start updates now" or "check updates now": run `monitor --no-notify`, then reply with a titled summary.
- "send latest 10 summaries" or "login and send top 10": run `list --limit 10`; reply with both incoming and outgoing categories, number the results, and retain the `module:intid` mapping for follow-up.
- "search <keywords>": run `search "<keywords>"`; reply with numbered results and ask which item numbers to download.
- "search <keywords> and download results": run `search "<keywords>"`, number the results, and download only the selected item ids unless the user explicitly asks for all results.
- "download all documents of item 5 to me": run `send --item 5 --delete-after`.
- "download items 2 and 4": run `send --item 2,4 --delete-after`.
- "download all results": run `send --all --delete-after`.

Target notes:
- This canonical skill is target-adaptable across supported install targets; target-specific paths should be resolved by the installing agent adapter or local environment.
- OpenClaw-specific rebuild material belongs to the OpenClaw adapter/rebuild plan, not to this ai-agents-skills canonical skill body.
