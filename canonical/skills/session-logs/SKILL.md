---
name: session-logs
description: Use when the user asks about earlier conversations, prior outputs, historical context, or past work that may live in Codex memories, Codex session logs, or optional legacy OpenClaw logs.
metadata:
  short-description: Search prior Codex and optional legacy sessions
---

# Session Logs

Use this skill to recover prior context from local artifacts.

## Search order

1. `~/.codex/memories/`
2. `~/.codex/history.jsonl`
3. `~/.codex/sessions/YYYY/MM/DD/*.jsonl`
4. `~/.codex/log/`
5. `~/.codex/archived_sessions/` and `~/.codex/session_index.jsonl` if those artifacts exist
6. `~/.openclaw/agents/.../sessions/` only as an optional legacy fallback when that path exists and the task specifically benefits from it

## When to use

- "What did we say before?"
- "Find the previous discussion about X"
- "Search older sessions"
- "What did Codex decide last time?"

## Tools

- use `functions.exec_command`
- prefer `rg` for filename and content filtering
- use `jq` for structured extraction from JSONL transcripts

## Useful patterns

Search memories first:

```bash
rg -n "phrase" ~/.codex/memories
```

Search local Codex sessions:

```bash
rg -l "phrase" ~/.codex/sessions ~/.codex/history.jsonl
```

Search optional archived Codex sessions:

```bash
rg -l "phrase" ~/.codex/archived_sessions ~/.codex/session_index.jsonl
```

Extract assistant text from Codex session JSONL:

```bash
jq -r 'select(.type == "response_item" and .payload.type == "message" and .payload.role == "assistant") | .payload.content[]? | select(.type == "output_text" or .type == "input_text") | .text' <session>.jsonl
```

Extract user text from Codex session JSONL:

```bash
jq -r 'select(.type == "response_item" and .payload.type == "message" and .payload.role == "user") | .payload.content[]? | select(.type == "input_text" or .type == "output_text") | .text' <session>.jsonl
```

Legacy OpenClaw fallback:

```bash
rg -l "phrase" ~/.openclaw/agents
```

## Rules

- Prefer `.codex` memories and `.codex` session artifacts before any legacy path.
- If `archived_sessions/` or `session_index.jsonl` exist, treat them as Codex-native sources before legacy OpenClaw logs.
- Use `~/.openclaw/...` only when that legacy path exists and is actually needed.
- Quote only the lines needed.
- Include the source path when it helps verification.
- If memory notes and raw session logs disagree, say so.
- Do not assume `~/.openclaw/agents/` exists locally; check first.
