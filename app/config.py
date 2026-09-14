import os
import secrets
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/config"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "docker-safeguard.db"

ICON_CACHE_DIR = DATA_DIR / "icons"
ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

TEMPLATES_USER_DIR = Path(os.environ.get("TEMPLATES_USER_DIR", "/unraid-templates"))

# Roots the UI is allowed to browse for backup destinations / restore sources.
BROWSE_ROOTS = {
    "shares": Path(os.environ.get("SHARES_ROOT", "/mnt/user")),
    "disks": Path(os.environ.get("DISKS_ROOT", "/mnt/disks")),
    "remotes": Path(os.environ.get("REMOTES_ROOT", "/mnt/remotes")),
}

# Host paths under these prefixes are treated as "appdata" and included in a
# backup by default; anything else (media libraries, downloads, etc.) is
# listed but excluded by default so archives stay focused and small.
APPDATA_PREFIXES = [
    Path("/mnt/user/appdata"),
    Path("/mnt/cache/appdata"),
]

TOOL_VERSION = "0.1.0"
SCHEMA_VERSION = 1
ARCHIVE_SUFFIX = ".safeguard.tar.zst"

SECRET_KEY_PATH = DATA_DIR / "secret_key"


def get_secret_key() -> str:
    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_text().strip()
    key = secrets.token_hex(32)
    SECRET_KEY_PATH.write_text(key)
    SECRET_KEY_PATH.chmod(0o600)
    return key


def is_appdata_path(host_path: str) -> bool:
    p = Path(host_path)
    return any(p == prefix or prefix in p.parents for prefix in APPDATA_PREFIXES)
