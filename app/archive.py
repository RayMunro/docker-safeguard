"""Archive format: a single zstd-compressed tar stream, built and read
without ever materializing the whole thing on disk uncompressed. Layout:

    manifest.json          (always first member)
    data/<archive_member>/...  (one subtree per included bind mount)
"""

from __future__ import annotations

import fnmatch
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from typing import Any, Callable

from .paths import dir_size_bytes

LogCb = Callable[[str], None] | None
ProgressCb = Callable[[int], None] | None


class _CountingWriter:
    def __init__(self, fileobj, total: int, on_progress: ProgressCb):
        self._fileobj = fileobj
        self._written = 0
        self._total = max(total, 1)
        self._on_progress = on_progress
        self._last_pct = -1

    def write(self, data: bytes) -> int:
        n = self._fileobj.write(data)
        self._written += n
        pct = int(self._written / self._total * 100)
        if pct != self._last_pct and self._on_progress:
            self._last_pct = pct
            self._on_progress(min(pct, 99))
        return n

    def flush(self) -> None:
        self._fileobj.flush()

    def close(self) -> None:
        self._fileobj.close()


def _exclude_filter(patterns: list[str]):
    if not patterns:
        return None

    def _filter(tarinfo: tarfile.TarInfo):
        base = os.path.basename(tarinfo.name)
        for pat in patterns:
            if fnmatch.fnmatch(tarinfo.name, pat) or fnmatch.fnmatch(base, pat):
                return None
        return tarinfo

    return _filter


def create_archive(
    dest_path: Path,
    manifest: dict[str, Any],
    exclude_patterns: list[str] | None = None,
    log_cb: LogCb = None,
    progress_cb: ProgressCb = None,
) -> int:
    """Writes manifest + included data paths to dest_path. Mutates
    manifest['data_paths'] entries with measured size_bytes. Returns the
    final compressed archive size in bytes."""
    included = [p for p in manifest["data_paths"] if p.get("included")]

    total = 0
    for p in included:
        size = dir_size_bytes(p["host_path"]) or 0
        p["size_bytes"] = size
        total += size
    if log_cb:
        log_cb(f"backing up {len(included)} path(s), ~{total} bytes total")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    zstd_proc = subprocess.Popen(
        ["zstd", "-T0", "-q", "-o", str(dest_path), "-"],
        stdin=subprocess.PIPE,
    )
    assert zstd_proc.stdin is not None
    writer = _CountingWriter(zstd_proc.stdin, total, progress_cb)
    exclude = _exclude_filter(exclude_patterns or [])

    try:
        with tarfile.open(fileobj=writer, mode="w|") as tar:
            manifest_bytes = json.dumps(manifest, indent=2, default=str).encode()
            info = tarfile.TarInfo(name="manifest.json")
            info.size = len(manifest_bytes)
            tar.addfile(info, io.BytesIO(manifest_bytes))

            for p in included:
                host_path = Path(p["host_path"])
                if not host_path.exists():
                    if log_cb:
                        log_cb(f"skipping missing path {host_path}")
                    continue
                arcname = f"data/{p['archive_member']}"
                if log_cb:
                    log_cb(f"archiving {host_path} -> {arcname}")
                tar.add(str(host_path), arcname=arcname, filter=exclude)
    finally:
        writer.close()
        zstd_proc.wait()

    if zstd_proc.returncode != 0:
        raise RuntimeError(f"zstd compression failed (exit code {zstd_proc.returncode})")
    if progress_cb:
        progress_cb(100)
    return dest_path.stat().st_size


def peek_manifest(archive_path: Path) -> dict[str, Any]:
    """Reads just the manifest.json member without decompressing the rest."""
    proc = subprocess.Popen(
        ["zstd", "-d", "-T0", "-q", "-c", str(archive_path)], stdout=subprocess.PIPE
    )
    assert proc.stdout is not None
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            member = tar.next()
            if member is None or member.name != "manifest.json":
                raise ValueError("manifest.json not found - not a Docker Safeguard archive?")
            extracted = tar.extractfile(member)
            if extracted is None:
                raise ValueError("could not read manifest.json from archive")
            return json.loads(extracted.read())
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def extract_archive(
    archive_path: Path,
    manifest: dict[str, Any],
    path_overrides: dict[str, str] | None = None,
    log_cb: LogCb = None,
    progress_cb: ProgressCb = None,
) -> None:
    path_overrides = path_overrides or {}
    arcname_to_dest: dict[str, str] = {}
    total = 0
    for p in manifest["data_paths"]:
        if not p.get("included"):
            continue
        dest = path_overrides.get(p["archive_member"], p["host_path"])
        arcname_to_dest[p["archive_member"]] = dest
        total += p.get("size_bytes") or 0

    proc = subprocess.Popen(
        ["zstd", "-d", "-T0", "-q", "-c", str(archive_path)], stdout=subprocess.PIPE
    )
    assert proc.stdout is not None
    written = 0
    last_pct = -1
    seen_roots: set[str] = set()
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for member in tar:
                if member.name == "manifest.json" or not member.name.startswith("data/"):
                    continue
                rel = member.name[len("data/") :]
                arcname, _, subpath = rel.partition("/")
                dest_root = arcname_to_dest.get(arcname)
                if not dest_root:
                    continue
                if dest_root not in seen_roots:
                    Path(dest_root).mkdir(parents=True, exist_ok=True)
                    seen_roots.add(dest_root)
                    if log_cb:
                        log_cb(f"restoring data into {dest_root}")
                member.name = subpath if subpath else "."
                tar.extract(member, path=dest_root, numeric_owner=True)
                written += member.size or 0
                if total:
                    pct = int(written / total * 100)
                    if pct != last_pct and progress_cb:
                        last_pct = pct
                        progress_cb(min(pct, 99))
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.wait()

    if proc.returncode not in (0, None):
        raise RuntimeError(f"zstd decompression failed (exit code {proc.returncode})")
    if progress_cb:
        progress_cb(100)


def verify_archive(archive_path: Path) -> dict[str, Any]:
    result = subprocess.run(["zstd", "-t", str(archive_path)], capture_output=True, text=True)
    info: dict[str, Any] = {
        "integrity_ok": result.returncode == 0,
        "stderr": result.stderr.strip(),
    }
    if info["integrity_ok"]:
        try:
            manifest = peek_manifest(archive_path)
            info["manifest_ok"] = True
            info["container_name"] = manifest["container"]["name"]
            info["created_at"] = manifest["created_at"]
        except Exception as exc:  # noqa: BLE001
            info["manifest_ok"] = False
            info["manifest_error"] = str(exc)
    return info
