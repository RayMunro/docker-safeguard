from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from .. import docker_client
from ..auth import require_login
from ..db import get_session
from ..models import AppSetting
from ..web import templates

router = APIRouter()


@router.get("/")
def dashboard(request: Request, user: str = Depends(require_login), session: Session = Depends(get_session)):
    docker_ok = docker_client.is_available()
    containers = []
    if docker_ok:
        containers = docker_client.list_containers()
        settings_by_name = {
            s.container_name: s for s in session.exec(select(AppSetting)).all()
        }
        for c in containers:
            c["settings"] = settings_by_name.get(c["name"])

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "docker_ok": docker_ok,
            "containers": containers,
        },
    )
