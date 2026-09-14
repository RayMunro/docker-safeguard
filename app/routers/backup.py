from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from .. import archive, docker_client, manifest as manifest_mod
from ..auth import require_login
from ..config import BROWSE_ROOTS
from ..db import get_session, session_scope
from ..jobs import Job, create_job, get_job, run_in_background
from ..models import AppSetting, BackupJob
from ..paths import classify_data_paths, dir_size_bytes, human_size
from ..web import templates

router = APIRouter(prefix="/backup")


@router.get("/{name}/options")
def backup_options(request: Request, name: str, user: str = Depends(require_login)):
    inspect_data = docker_client.inspect(name)
    mounts = docker_client.bind_mounts(inspect_data)
    data_paths = classify_data_paths(mounts)
    for p in data_paths:
        size = dir_size_bytes(p["host_path"], timeout=8)
        p["size_display"] = human_size(size) if size is not None else "unknown"

    return templates.TemplateResponse(
        request,
        "backup_options.html",
        {
            "container_name": name,
            "data_paths": data_paths,
            "browse_roots": list(BROWSE_ROOTS.keys()),
        },
    )


def _do_backup(job: Job, container_name: str, dest_dir: Path, stop_first: bool, exclude_patterns: list[str], selection):
    job.log(f"starting backup of {container_name}")
    m = manifest_mod.build_manifest(container_name, data_path_selection=selection)
    dest_file = dest_dir / manifest_mod.archive_filename(m)

    was_running = docker_client.is_running(container_name)
    stopped = False
    try:
        if stop_first and was_running:
            job.log("stopping container for a consistent backup")
            docker_client.stop(container_name)
            stopped = True

        size = archive.create_archive(
            dest_file,
            m,
            exclude_patterns=exclude_patterns,
            log_cb=job.log,
            progress_cb=job.set_percent,
        )
        job.log(f"archive written: {dest_file} ({human_size(size)})")
        job.result = {"archive_path": str(dest_file), "size_bytes": size}
    finally:
        if stopped:
            job.log("restarting container")
            docker_client.start(container_name)

    with session_scope() as session:
        setting = session.get(AppSetting, container_name)
        if not setting:
            setting = AppSetting(container_name=container_name)
        setting.last_backup_at = datetime.now(timezone.utc)
        setting.last_backup_status = "success"
        setting.last_backup_path = str(dest_file)
        session.add(setting)
        session.add(
            BackupJob(
                job_uuid=job.id,
                container_name=container_name,
                kind="backup",
                finished_at=datetime.now(timezone.utc),
                status="success",
                archive_path=str(dest_file),
                size_bytes=job.result["size_bytes"],
            )
        )
        session.commit()


@router.post("/{name}/run")
def backup_run(
    request: Request,
    name: str,
    destination_root: str = Form(...),
    destination_subpath: str = Form(""),
    stop_before_backup: bool = Form(False),
    exclude_patterns: str = Form(""),
    included: list[str] = Form([]),
    user: str = Depends(require_login),
    session: Session = Depends(get_session),
):
    root = BROWSE_ROOTS[destination_root]
    dest_dir = (root / destination_subpath.lstrip("/")).resolve()
    if dest_dir == root.resolve():
        # Never write archives loose at a share's root - they'd end up
        # sitting unnoticed among unrelated top-level shares/folders.
        dest_dir = dest_dir / "backups" / name

    inspect_data = docker_client.inspect(name)
    mounts = docker_client.bind_mounts(inspect_data)
    data_paths = classify_data_paths(mounts)
    selection = [
        {"index": p["index"], "included": str(p["index"]) in included} for p in data_paths
    ]
    excludes = [line.strip() for line in exclude_patterns.splitlines() if line.strip()]

    job = create_job("backup", name)
    run_in_background(
        lambda j: _do_backup(j, name, dest_dir, stop_before_backup, excludes, selection), job
    )
    return RedirectResponse(f"/backup/jobs/{job.id}", status_code=303)


@router.get("/jobs/{job_id}")
def backup_job_page(request: Request, job_id: str, user: str = Depends(require_login)):
    job = get_job(job_id)
    return templates.TemplateResponse(
        request, "job_progress.html", {"job_id": job_id, "kind": "backup", "job": job}
    )


@router.get("/jobs/{job_id}/status")
def backup_job_status(request: Request, job_id: str, user: str = Depends(require_login)):
    job = get_job(job_id)
    snap = job.snapshot() if job else None
    return templates.TemplateResponse(
        request, "partials/job_status.html", {"job_id": job_id, "kind": "backup", "job": snap}
    )
