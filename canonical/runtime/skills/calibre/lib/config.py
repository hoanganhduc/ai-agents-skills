"""Configuration loader for Calibre skill.

Priority: environment variables > secrets.json > config.json > defaults.
"""

import os
import json
import sys

WORKSPACE = os.environ.get("AAS_RUNTIME_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE") or os.path.join(os.path.expanduser("~"), ".codex", "runtime", "workspace")
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "gdrive_folder_id": "",
    "gdrive_credentials_file": "",
    "staging_dir": os.path.join(WORKSPACE, "data", "calibre", "staging"),
    "cache_path": os.path.join(WORKSPACE, "data", "calibre", "cache", "library.json"),
    "db_local_path": os.path.join(WORKSPACE, "data", "calibre", "cache", "metadata.db"),
    "cache_max_age_hours": 24,
    "default_send_channel": "telegram",
    "isbn_lookup_url": "https://openlibrary.org/api/books",
    "preferred_format": "epub",
    "max_search_results": 25,
}


def load_config(require=None):
    config = dict(DEFAULTS)

    # Load config.json from skill dir
    cfg_path = os.path.join(SKILL_DIR, "config.json")
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            config.update(json.load(f))

    # Load secrets file
    secrets_file = os.environ.get(
        "OPENCLAW_SECRETS_FILE",
        os.path.join(WORKSPACE, ".secrets.json"),
    )
    if os.path.exists(secrets_file):
        with open(secrets_file) as f:
            secrets = json.load(f)
        for key in ("GDRIVE_CREDENTIALS",):
            if key in secrets:
                config[key] = secrets[key]
        # Allow CALIBRE_GDRIVE_FOLDER_ID override in secrets
        if "CALIBRE_GDRIVE_FOLDER_ID" in secrets:
            config["gdrive_folder_id"] = secrets["CALIBRE_GDRIVE_FOLDER_ID"]

    # Environment variable overrides
    for env_key, cfg_key in [
        ("GDRIVE_CREDENTIALS", "GDRIVE_CREDENTIALS"),
        ("CALIBRE_GDRIVE_FOLDER_ID", "gdrive_folder_id"),
        ("CALIBRE_STAGING_DIR", "staging_dir"),
    ]:
        val = os.environ.get(env_key)
        if val:
            config[cfg_key] = val

    # Ensure directories exist
    os.makedirs(config["staging_dir"], exist_ok=True)
    os.makedirs(os.path.dirname(config["cache_path"]), exist_ok=True)

    # Validate required keys
    if require:
        missing = [k for k in require if not config.get(k)]
        if missing:
            print(json.dumps({
                "status": "error",
                "message": f"Missing required config: {', '.join(missing)}. "
                           f"Set in skills/calibre/config.json or secrets file.",
            }))
            sys.exit(1)

    return config
