import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from .. import archive, docker_client
from ..auth import require_login
from ..config import ARCHIVE_SUFFIX, BROWSE_ROOTS, UPLOAD_DIR, is_under_known_mount
from ..db import session_scope
from ..jobs import Job, create_job, get_job, run_in_background
from ..models import AppSetting, BackupJob
from ..paths import human_size
from ..template_xml import write_template
from ..web import templates

router = APIRouter(prefix="/restore")


def _resolve_source(root: str, path: str, token: str) -> Path:
    if token:
        candidate = (UPLOAD_DIR / token).resolve()
        if UPLOAD_DIR.resolve() not in candidate.parents:
            raise ValueError("invalid upload token")
        return candidate
    if root not in BROWSE_ROOTS:
        raise ValueError("unknown source root")
    base = BROWSE_ROOTS[root].resolve()
    candidate = (base / path.lstrip("/")).resolve()
    if base not in candidate.parents:
        raise ValueError("path escapes browse root")
    return candidate


@router.get("")
def restore_home(request: Request, user: str = Depends(require_login)):
    return templates.TemplateResponse(
        request,
        "restore_browse.html",
        {"browse_roots": list(BROWSE_ROOTS.keys())},
    )


@router.post("/upload")
def restore_upload(request: Request, file: UploadFile, user: str = Depends(require_login)):
    token = f"{uuid.uuid4().hex}{ARCHIVE_SUFFIX}"
    dest = UPLOAD_DIR / token
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return RedirectResponse(f"/restore/preview?token={token}", status_code=303)


def _build_preview(archive_path: Path) -> dict[str, Any]:
    m = archive.peek_manifest(archive_path)
    name = m["container"]["name"]
    image = m["container"]["image"]
    raw = m["container"].get("raw_inspect", {})
    config = raw.get("Config", {}) or {}
    host_config = raw.get("HostConfig", {}) or {}

    data_conflicts = []
    unreachable_paths = []
    for p in m["data_paths"]:
        if not p.get("included"):
            continue
        host_path = Path(p["host_path"])
        reachable = is_under_known_mount(p["host_path"])
        if not reachable:
            unreachable_paths.append(p["host_path"])
        exists_nonempty = False
        if reachable and host_path.exists():
            try:
                exists_nonempty = any(host_path.iterdir())
            except (NotADirectoryError, PermissionError):
                exists_nonempty = True
        data_conflicts.append(
            {
                "archive_member": p["archive_member"],
                "host_path": p["host_path"],
                "container_path": p["container_path"],
                "exists_nonempty": exists_nonempty,
                "reachable": reachable,
                "size_display": human_size(p.get("size_bytes")),
            }
        )

    return {
        "unreachable_paths": unreachable_paths,
        "manifest": m,
        "name": name,
        "image": image,
        "created_at": m.get("created_at"),
        "source_server": m.get("source_server"),
        "container_exists": docker_client.container_exists(name),
        "image_locally_present": docker_client.image_exists_locally(image),
        "data_conflicts": data_conflicts,
        "ports": host_config.get("PortBindings") or {},
        "env_count": len(config.get("Env") or []),
        "restart_policy": (host_config.get("RestartPolicy") or {}).get("Name"),
        "named_volumes": m.get("named_volumes") or [],
        "total_size_display": human_size(
            sum(p.get("size_bytes") or 0 for p in m["data_paths"] if p.get("included"))
        ),
        "has_template": bool(m.get("template_xml")),
    }


@router.get("/preview")
def restore_preview_from_query(
    request: Request,
    root: str = "",
    path: str = "",
    token: str = "",
    user: str = Depends(require_login),
):
    try:
        src = _resolve_source(root, path, token)
        ctx = _build_preview(src)
    except (ValueError, FileNotFoundError) as exc:
        return templates.TemplateResponse(
            request, "restore_browse.html", {"browse_roots": list(BROWSE_ROOTS.keys()), "error": str(exc)}
        )
    ctx.update({"source_root": root, "source_path": path, "source_token": token})
    return templates.TemplateResponse(request, "restore_preview.html", ctx)


def _do_restore(job: Job, archive_path: Path, clear_existing_data: bool, remove_existing_container: bool):
    job.log(f"reading manifest from {archive_path}")
    m = archive.peek_manifest(archive_path)
    name = m["container"]["name"]
    image = m["container"]["image"]
    raw = m["container"]["raw_inspect"]

    unreachable = [
        p["host_path"]
        for p in m["data_paths"]
        if p.get("included") and not is_under_known_mount(p["host_path"])
    ]
    if unreachable:
        raise RuntimeError(
            "Refusing to restore: these paths aren't reachable through any of this "
            "app's mounted folders, so the data would be written inside the "
            "container itself and lost, not to real storage: " + ", ".join(unreachable)
        )

    if remove_existing_container and docker_client.container_exists(name):
        job.log(f"removing existing container {name}")
        docker_client.remove(name, force=True)

    if clear_existing_data:
        for p in m["data_paths"]:
            if not p.get("included"):
                continue
            host_path = Path(p["host_path"])
            if host_path.exists():
                job.log(f"clearing existing data at {host_path}")
                shutil.rmtree(host_path, ignore_errors=True)

    job.log(f"pulling image {image}")
    try:
        docker_client.pull_image(image, log_cb=job.log)
    except Exception as exc:  # noqa: BLE001
        job.log(f"warning: could not pull {image} ({exc}); trying local copy if present")
        if not docker_client.image_exists_locally(image):
            raise

    job.log("restoring data")
    archive.extract_archive(archive_path, m, log_cb=job.log, progress_cb=job.set_percent)

    if m.get("template_xml"):
        job.log("restoring unRAID template")
        write_template(name, m["template_xml"])

    job.log(f"creating container {name}")
    docker_client.create_container_from_inspect(raw, name)
    job.log("container started")

    job.result = {"container_name": name, "image": image}

    with session_scope() as session:
        setting = session.get(AppSetting, name)
        if not setting:
            setting = AppSetting(container_name=name)
        session.add(setting)
        session.add(
            BackupJob(
                job_uuid=job.id,
                container_name=name,
                kind="restore",
                finished_at=datetime.now(timezone.utc),
                status="success",
                archive_path=str(archive_path),
            )
        )
        session.commit()


@router.post("/run")
def restore_run(
    request: Request,
    source_root: str = Form(""),
    source_path: str = Form(""),
    source_token: str = Form(""),
    clear_existing_data: bool = Form(False),
    remove_existing_container: bool = Form(False),
    user: str = Depends(require_login),
):
    src = _resolve_source(source_root, source_path, source_token)
    m = archive.peek_manifest(src)
    job = create_job("restore", m["container"]["name"])
    run_in_background(
        lambda j: _do_restore(j, src, clear_existing_data, remove_existing_container), job
    )
    return RedirectResponse(f"/restore/jobs/{job.id}", status_code=303)


@router.get("/jobs/{job_id}")
def restore_job_page(request: Request, job_id: str, user: str = Depends(require_login)):
    job = get_job(job_id)
    return templates.TemplateResponse(
        request, "job_progress.html", {"job_id": job_id, "kind": "restore", "job": job}
    )


@router.get("/jobs/{job_id}/status")
def restore_job_status(request: Request, job_id: str, user: str = Depends(require_login)):
    job = get_job(job_id)
    snap = job.snapshot() if job else None
    return templates.TemplateResponse(
        request, "partials/job_status.html", {"job_id": job_id, "kind": "restore", "job": snap}
    )
