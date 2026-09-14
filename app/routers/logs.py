from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from ..auth import require_login
from ..db import get_session
from ..models import BackupJob
from ..web import templates

router = APIRouter(prefix="/logs")


@router.get("")
def logs_home(request: Request, user: str = Depends(require_login), session: Session = Depends(get_session)):
    jobs = session.exec(select(BackupJob).order_by(BackupJob.started_at.desc()).limit(100)).all()
    return templates.TemplateResponse(request, "logs.html", {"jobs": jobs})
