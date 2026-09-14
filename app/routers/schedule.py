from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from .. import docker_client
from ..auth import require_login
from ..config import BROWSE_ROOTS
from ..db import get_session
from ..models import Schedule
from ..web import templates

router = APIRouter(prefix="/schedule")


def _presets():
    return {
        "daily": "0 3 * * *",
        "weekly": "0 3 * * 0",
        "monthly": "0 3 1 * *",
    }


@router.get("")
def schedule_home(request: Request, user: str = Depends(require_login), session: Session = Depends(get_session)):
    schedules = session.exec(select(Schedule)).all()
    container_names = [c["name"] for c in docker_client.list_containers()] if docker_client.is_available() else []
    return templates.TemplateResponse(
        request,
        "schedule.html",
        {
            "schedules": schedules,
            "container_names": container_names,
            "browse_roots": list(BROWSE_ROOTS.keys()),
            "presets": _presets(),
        },
    )


@router.post("/add")
def schedule_add(
    request: Request,
    container_name: str = Form(...),
    frequency: str = Form("daily"),
    custom_cron: str = Form(""),
    destination_root: str = Form(...),
    destination_subpath: str = Form(""),
    retention: int = Form(5),
    user: str = Depends(require_login),
    session: Session = Depends(get_session),
):
    cron = custom_cron.strip() if frequency == "custom" else _presets().get(frequency, _presets()["daily"])
    destination = f"{destination_root}:{destination_subpath.lstrip('/')}"
    sched = Schedule(
        container_name=container_name,
        cron=cron,
        destination=destination,
        retention=retention,
        enabled=True,
    )
    session.add(sched)
    session.commit()

    from ..scheduler import reload_schedules

    reload_schedules()
    return RedirectResponse("/schedule", status_code=303)


@router.post("/{schedule_id}/toggle")
def schedule_toggle(schedule_id: int, user: str = Depends(require_login), session: Session = Depends(get_session)):
    sched = session.get(Schedule, schedule_id)
    if sched:
        sched.enabled = not sched.enabled
        session.add(sched)
        session.commit()
        from ..scheduler import reload_schedules

        reload_schedules()
    return RedirectResponse("/schedule", status_code=303)


@router.post("/{schedule_id}/delete")
def schedule_delete(schedule_id: int, user: str = Depends(require_login), session: Session = Depends(get_session)):
    sched = session.get(Schedule, schedule_id)
    if sched:
        session.delete(sched)
        session.commit()
        from ..scheduler import reload_schedules

        reload_schedules()
    return RedirectResponse("/schedule", status_code=303)
