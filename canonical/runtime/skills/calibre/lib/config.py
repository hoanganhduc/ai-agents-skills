"""Configuration loader for Calibre skill.

Priority: environment variables > secrets.json > config.json > defaults.
"""

import os
import json
import stat
import sys
from pathlib import Path

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
CALIBRE_SECRET_KEYS = {"GDRIVE_CREDENTIALS", "CALIBRE_GDRIVE_FOLDER_ID"}
MAX_SECRETS_FILE_BYTES = 65_536


class _JsonPairs(list):
    """Distinguish a JSON object from a JSON array during strict parsing."""


def _strict_pairs(pairs):
    seen = set()
    for key, _value in pairs:
        if key in seen:
            raise ValueError("Calibre secrets projection contains a duplicate key")
        seen.add(key)
    return _JsonPairs(pairs)


def _open_directory_nofollow(path):
    absolute = Path(os.path.abspath(path))
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute.anchor or os.sep, flags)
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _secret_stability(info):
    return (
        int(info.st_dev), int(info.st_ino), int(info.st_mode), int(info.st_uid),
        int(info.st_nlink), int(info.st_size), int(info.st_mtime_ns), int(info.st_ctime_ns),
    )


def _read_private_secret_bytes(path):
    """Read a private JSON projection through a stable no-follow descriptor."""

    if os.name == "nt":  # pragma: no cover - flat secrets are projected by run_skill.ps1
        try:
            os.lstat(os.path.abspath(path))
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ValueError("Calibre secrets projection could not be read securely") from exc
        raise ValueError("native Windows secret files require the managed projection runner")
    absolute = Path(os.path.abspath(path))
    parent_descriptor = None
    file_descriptor = None
    try:
        parent_descriptor = _open_directory_nofollow(absolute.parent)
        path_info = os.stat(absolute.name, dir_fd=parent_descriptor, follow_symlinks=False)
        file_descriptor = os.open(
            absolute.name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_descriptor,
        )
        before = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or int(before.st_uid) != int(os.geteuid())
            or stat.S_IMODE(before.st_mode) & 0o077
            or int(before.st_nlink) != 1
            or int(before.st_size) > MAX_SECRETS_FILE_BYTES
            or (int(path_info.st_dev), int(path_info.st_ino))
            != (int(before.st_dev), int(before.st_ino))
        ):
            raise ValueError("Calibre secrets projection is not private and single-link")
        chunks = []
        remaining = MAX_SECRETS_FILE_BYTES + 1
        while remaining > 0:
            chunk = os.read(file_descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(file_descriptor)
        if len(payload) > MAX_SECRETS_FILE_BYTES:
            raise ValueError("Calibre secrets projection is oversized")
        if _secret_stability(before) != _secret_stability(after):
            raise ValueError("Calibre secrets projection changed while reading")
        return payload
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("Calibre secrets projection could not be read securely") from exc
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def _load_secret_projection(path):
    payload = _read_private_secret_bytes(path)
    if payload is None:
        return None
    try:
        pairs = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Calibre secrets projection is not valid UTF-8 JSON") from exc
    if not isinstance(pairs, _JsonPairs):
        raise ValueError("Calibre secrets projection must contain one JSON object")
    values = {}
    for key, value in pairs:
        if key not in CALIBRE_SECRET_KEYS:
            raise ValueError("Calibre secrets projection contains unsupported keys")
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or "\x00" in value
        ):
            raise ValueError("Calibre secrets projection values must be non-empty strings")
        values[key] = value
    return values


def load_config(require=None):
    config = dict(DEFAULTS)

    # Load config.json from skill dir
    cfg_path = os.path.join(SKILL_DIR, "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                config.update(json.load(f))
        except (json.JSONDecodeError, OSError) as exc:
            # Every cal command reports through the envelope the require branch
            # below uses; a config file that exists but cannot be parsed reached
            # the caller as a traceback instead.
            print(json.dumps({
                "status": "error",
                "message": f"Config file could not be read: {cfg_path}: {exc}",
            }))
            sys.exit(1)

    # Load only the dedicated narrow Calibre projection. Broad runtime secret
    # files are intentionally not authorities for this skill.
    secrets_file = os.environ.get("AAS_CALIBRE_SECRETS_FILE") or os.path.join(
        os.path.expanduser("~"), ".config", "ai-agents-skills", "calibre-secrets.json"
    )
    secrets = _load_secret_projection(secrets_file)
    if secrets is None and os.environ.get("AAS_CALIBRE_SECRETS_FILE"):
        raise ValueError("selected Calibre secrets projection is missing")
    if secrets is not None:
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
