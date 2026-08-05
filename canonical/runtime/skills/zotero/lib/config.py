"""Config loader for the zot CLI with environment-first secret handling."""

import json
import os
import stat
import sys
from pathlib import Path

REQUIRED_FOR_SEARCH = ["zotero_user_id"]
SECRETS_KEYS = [
    "ZOTERO_API_KEY",
    "WEBDAV_PASSWORD",
    "GDRIVE_CREDENTIALS",
    "SEMANTIC_SCHOLAR_API_KEY",
]
MAX_SECRETS_FILE_BYTES = 65_536

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
    env_secrets = os.environ.get("AAS_ZOTERO_SECRETS_FILE")
    if env_secrets:
        return env_secrets

    candidates = [
        os.path.join(os.path.expanduser("~"), ".config", "ai-agents-skills", "zotero-secrets.json"),
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


class _JsonPairs(list):
    """Distinguish a JSON object from a JSON array during strict parsing."""


def _strict_pairs(pairs):
    seen = set()
    for key, _value in pairs:
        if key in seen:
            raise ValueError("Zotero secrets projection contains a duplicate key")
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
            raise ValueError("Zotero secrets projection is not private and single-link")
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
            raise ValueError("Zotero secrets projection is oversized")
        if _secret_stability(before) != _secret_stability(after):
            raise ValueError("Zotero secrets projection changed while reading")
        return payload
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("Zotero secrets projection could not be read securely") from exc
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
        raise ValueError("Zotero secrets projection is not valid UTF-8 JSON") from exc
    if not isinstance(pairs, _JsonPairs):
        raise ValueError("Zotero secrets projection must contain one JSON object")
    values = {}
    allowed = set(SECRETS_KEYS)
    for key, value in pairs:
        if key not in allowed:
            raise ValueError("Zotero secrets projection contains unsupported keys")
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or "\x00" in value
        ):
            raise ValueError("Zotero secrets projection values must be non-empty strings")
        values[key] = value
    return values


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
        file_secrets = _load_secret_projection(secrets_path)
        if file_secrets is None and os.environ.get("AAS_ZOTERO_SECRETS_FILE"):
            raise ValueError("selected Zotero secrets projection is missing")
        if file_secrets is not None:
            for key in missing:
                if key in file_secrets and file_secrets[key]:
                    secrets[key] = file_secrets[key]

    return secrets


def load_config(require=None, config_path=None):
    """Load config + secrets. Returns merged dict.

    Args:
        require: list of required config keys (beyond REQUIRED_FOR_SEARCH).
                 Raises SystemExit if any are missing.
        config_path: explicit config file; defaults to the workspace location.
    """
    config_path = config_path or _find_config_path()
    if not os.path.exists(config_path):
        print(json.dumps({
            "status": "error",
            "action": "config",
            "message": f"Config file not found: {config_path}",
            "code": "CONFIG_MISSING",
        }))
        sys.exit(1)

    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    # This legacy field used to appear in the non-secret config example. Never
    # use a credential from a normally 0644 config file; the uppercase secret
    # authority below comes from the managed environment or private secrets file.
    config.pop("semantic_scholar_api_key", None)

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
