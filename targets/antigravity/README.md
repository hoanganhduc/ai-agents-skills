# Antigravity Target

Antigravity is a full install target for ai-agents-skills.

The installer writes Antigravity-native user-global surfaces under:

- `~/.gemini/antigravity-cli/skills/<skill>.md`
- `~/.gemini/GEMINI.md`
- `~/.gemini/antigravity-cli/plugins/ai-agents-skills/plugin.json`
- `~/.gemini/antigravity-cli/plugins/ai-agents-skills/agents/`
- `~/.gemini/antigravity-cli/plugins/ai-agents-skills/rules/`
- `~/.gemini/antigravity-cli/plugins/ai-agents-skills/templates/`
- `~/.gemini/antigravity-cli/plugins/ai-agents-skills/mcp_config.json`
- `~/.gemini/antigravity-cli/plugins/ai-agents-skills/hooks.json`
- `~/.gemini/antigravity-cli/settings.json`

Auto mode installs flat Markdown skill adapters in the documented global skill
directory. Plugin payloads are grouped under the managed `ai-agents-skills`
plugin so personas, rules, templates, hook config, and MCP config have a native
Antigravity package boundary.

The managed `mcp_config.json`, `hooks.json`, and `settings.json` scaffolds are
no-op JSON files by default. They establish reversible installer ownership of
the native surfaces without enabling unknown servers, commands, hooks, models,
API keys, or credentials.

To adapt existing Antigravity settings to the current machine or repository
without copying a static JSON blob, use:

```bash
./installer/bootstrap.sh --root "$HOME" antigravity-fixup --workspace /path/to/repo --apply
```

This preserves existing settings, trims malformed `gcp.project` whitespace,
adds the selected workspace to `trustedWorkspaces`, and disables empty
status-line stubs that can surface misleading cwd metadata.

Project-local `.agents/` directories do not activate the global Antigravity
target by themselves.

Native Antigravity smoke, when the `agy` CLI is available, uses isolated home
and app-data directories and checks:

- `agy --help`
- `agy plugin list`
- managed global skill file shape
- managed plugin/config file shape

Native Windows execution still requires a host-side smoke pass before claiming
real Windows loader/runtime verification.
