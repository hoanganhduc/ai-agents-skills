"""Config loader for the zot CLI. SecretRef-aware with env var + file fallback."""

import json
import os
import sys

REQUIRED_FOR_SEARCH = ["zotero_user_id"]
SECRETS_KEYS = ["ZOTERO_API_KEY", "WEBDAV_PASSWORD", "GDRIVE_CREDENTIALS"]

DEFAULT_CONFIG = {
    "translation_server": "http://host.docker.internal:1969",
    "gdrive_share_permission": "anyone_with_link",
    "auto_catalog_threshold": 80,
    "cache_max_age_hours": 24,
    "zotfile_pattern": "{author}_{year}_{title}",
    "wsl_translation_distro": "Ubuntu-24.04",
    "wsl_translation_repo": "~/zotero-translation-server",
}


def default_workspace():
    env_workspace = os.environ.get("AAS_RUNTIME_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE")
    if env_workspace:
        return env_workspace

    candidates = []
    userprofile = os.environ.get("USERPROFILE")
    home = os.path.expanduser("~")

    if userprofile:
        candidates.extend([
            os.path.join(userprofile, ".codex", "runtime", "workspace"),
        ])

    candidates.extend([
        os.path.join(home, ".codex", "runtime", "workspace"),
    ])

    for path in candidates:
        if path and os.path.exists(path):
            return path

    if userprofile:
        return os.path.join(userprofile, ".codex", "runtime", "workspace")
    return os.path.join(home, ".codex", "runtime", "workspace")


def default_secrets_path():
    env_secrets = os.environ.get("AAS_SECRETS_FILE") or os.environ.get("OPENCLAW_SECRETS_FILE")
    if env_secrets:
        return env_secrets

    workspace = default_workspace()
    candidates = [
        os.path.join(workspace, ".secrets.json"),
        os.path.join(os.path.expanduser("~"), ".codex", "runtime", "workspace", ".secrets.json"),
    ]

    for path in candidates:
        if path and os.path.exists(path):
            return path
    return candidates[0]


def _find_config_path():
    workspace = default_workspace()
    return os.path.join(workspace, "skills", "zotero", "config.json")


def _find_secrets_path():
    return default_secrets_path()


def _load_secrets():
    """Load secrets: env vars first, fall back to secrets.json file."""
    secrets = {}
    for key in SECRETS_KEYS:
        val = os.environ.get(key)
        if val:
            secrets[key] = val

    missing = [k for k in SECRETS_KEYS if k not in secrets]
    if missing:
        secrets_path = _find_secrets_path()
        if os.path.exists(secrets_path):
            with open(secrets_path) as f:
                file_secrets = json.load(f)
            for key in missing:
                if key in file_secrets and file_secrets[key]:
                    secrets[key] = file_secrets[key]

    return secrets


def load_config(require=None):
    """Load config + secrets. Returns merged dict.

    Args:
        require: list of required config keys (beyond REQUIRED_FOR_SEARCH).
                 Raises SystemExit if any are missing.
    """
    config_path = _find_config_path()
    if not os.path.exists(config_path):
        print(json.dumps({
            "status": "error",
            "action": "config",
            "message": f"Config file not found: {config_path}",
            "code": "CONFIG_MISSING",
        }))
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    # Apply defaults for missing optional keys
    for key, default in DEFAULT_CONFIG.items():
        if key not in config or config[key] == "":
            config[key] = default

    # Merge secrets
    secrets = _load_secrets()
    config.update(secrets)

    # Validate required fields
    required = list(REQUIRED_FOR_SEARCH)
    if require:
        required.extend(require)

    missing = [k for k in required if not config.get(k)]
    if missing:
        print(json.dumps({
            "status": "error",
            "action": "config",
            "message": f"Missing required config: {', '.join(missing)}",
            "code": "CONFIG_MISSING",
        }))
        sys.exit(1)

    # Resolve workspace path
    config["workspace"] = default_workspace()
    config["staging_dir"] = os.path.join(
        config["workspace"], "data", "research", "zotero", "staging"
    )

    return config
