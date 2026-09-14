from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .config import BROWSE_ROOTS, is_appdata_path, is_under_known_mount


def path_to_root_subpath(path: str) -> tuple[str, str] | None:
    """Inverse of joining a browse root + subpath: given an absolute host
    path, find which BROWSE_ROOTS entry it falls under and the subpath
    relative to it. None if it's not under any of them."""
    p = Path(path).resolve()
    for key, root in BROWSE_ROOTS.items():
        root = root.resolve()
        if p == root:
            return key, ""
        if root in p.parents:
            return key, str(p.relative_to(root))
    return None


def human_size(num_bytes: int | None) -> str:
    if not num_bytes:
        return "0 B"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def dir_size_bytes(path: str, timeout: int = 20) -> int | None:
    """Fast approximate size via `du`. Returns None if it can't be measured
    (missing path, timeout on huge trees) rather than blocking the UI."""
    try:
        result = subprocess.run(
            ["du", "-sb", path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0 and result.stdout:
            return int(result.stdout.split()[0])
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def classify_data_paths(mounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """mounts: list of {source, destination, mode} bind mounts from
    docker_client.bind_mounts(). Returns enriched entries with a stable
    archive_member key and a default include/exclude decision."""
    out = []
    for idx, m in enumerate(mounts):
        source = m["source"]
        reachable = is_under_known_mount(source)
        out.append(
            {
                "index": idx,
                "archive_member": f"mount{idx}",
                "host_path": source,
                "container_path": m["destination"],
                "mode": m["mode"],
                "reachable": reachable,
                # Only offer a path as backupable if it's both plausibly
                # appdata AND actually reachable through one of this app's
                # mounts - a host system path (e.g. a plugin's own mount
                # point, like Tailscale's container hook) is never real
                # per-app data worth archiving, whether or not it happens
                # to live under /mnt/user/appdata.
                "included_default": reachable and is_appdata_path(source),
            }
        )
    return out


def safe_browse_path(root_key: str, subpath: str) -> Path:
    if root_key not in BROWSE_ROOTS:
        raise ValueError(f"unknown browse root {root_key!r}")
    root = BROWSE_ROOTS[root_key]
    candidate = (root / subpath.lstrip("/")).resolve()
    if root.resolve() not in candidate.parents and candidate != root.resolve():
        raise ValueError("path escapes browse root")
    return candidate


def list_directory(root_key: str, subpath: str = "") -> dict[str, Any]:
    target = safe_browse_path(root_key, subpath)
    entries = []
    if target.exists() and target.is_dir():
        try:
            for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
                if child.is_dir() and not child.is_symlink():
                    entries.append({"name": child.name, "is_dir": True})
                elif child.is_file() and child.name.endswith(".safeguard.tar.zst"):
                    entries.append(
                        {
                            "name": child.name,
                            "is_dir": False,
                            "size": human_size(child.stat().st_size),
                        }
                    )
        except PermissionError:
            pass
    resolved_root = BROWSE_ROOTS[root_key].resolve()
    rel = target.relative_to(resolved_root) if target != resolved_root else Path("")
    return {
        "root_key": root_key,
        "subpath": str(rel) if str(rel) != "." else "",
        "entries": entries,
        "full_path": str(target),
    }
