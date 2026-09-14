from __future__ import annotations

import logging
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import select

from .config import BROWSE_ROOTS
from .db import session_scope
from .jobs import create_job, run_in_background
from .models import Schedule
from .routers.backup import _do_backup

log = logging.getLogger("docker_safeguard.scheduler")

_scheduler = BackgroundScheduler()


def _run_scheduled_backup(schedule_id: int) -> None:
    with session_scope() as session:
        sched = session.get(Schedule, schedule_id)
        if not sched or not sched.enabled:
            return
        container_name = sched.container_name
        root_key, _, subpath = sched.destination.partition(":")
        retention = sched.retention

    root = BROWSE_ROOTS.get(root_key)
    if not root:
        log.warning("schedule %s has unknown destination root %r", schedule_id, root_key)
        return
    dest_dir = (root / subpath.lstrip("/")).resolve()
    if dest_dir == root.resolve():
        dest_dir = dest_dir / "backups" / container_name

    job = create_job("backup", container_name)
    run_in_background(lambda j: _do_backup(j, container_name, dest_dir, True, [], None), job)

    if retention and retention > 0:
        _apply_retention(dest_dir, container_name, retention)


def _apply_retention(dest_dir: Path, container_name: str, retention: int) -> None:
    try:
        candidates = sorted(
            [p for p in dest_dir.glob(f"{container_name}__*.safeguard.tar.zst")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in candidates[retention:]:
            old.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("retention cleanup failed for %s: %s", container_name, exc)


def reload_schedules() -> None:
    for job in list(_scheduler.get_jobs()):
        job.remove()
    with session_scope() as session:
        schedules = session.exec(select(Schedule).where(Schedule.enabled == True)).all()  # noqa: E712
    for sched in schedules:
        try:
            trigger = CronTrigger.from_crontab(sched.cron)
        except ValueError as exc:
            log.warning("bad cron %r for schedule %s: %s", sched.cron, sched.id, exc)
            continue
        _scheduler.add_job(
            _run_scheduled_backup,
            trigger=trigger,
            args=[sched.id],
            id=f"schedule-{sched.id}",
            replace_existing=True,
        )


def start_scheduler() -> None:
    if not _scheduler.running:
        _scheduler.start()
    reload_schedules()
