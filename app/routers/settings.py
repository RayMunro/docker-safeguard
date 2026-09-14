from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from .. import archive
from ..auth import current_username, hash_password, require_login, verify_password
from ..config import BROWSE_ROOTS
from ..db import get_session
from ..models import AppConfig, User
from ..routers.restore import _resolve_source
from ..web import templates

router = APIRouter(prefix="/settings")


def _get_config(session: Session, key: str, default: str = "") -> str:
    row = session.get(AppConfig, key)
    return row.value if row else default


def _set_config(session: Session, key: str, value: str) -> None:
    row = session.get(AppConfig, key)
    if row:
        row.value = value
    else:
        row = AppConfig(key=key, value=value)
    session.add(row)
    session.commit()


@router.get("")
def settings_home(request: Request, user: str = Depends(require_login), session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "webhook_url": _get_config(session, "webhook_url"),
            "browse_roots": list(BROWSE_ROOTS.keys()),
        },
    )


@router.post("/webhook")
def settings_webhook(
    request: Request,
    webhook_url: str = Form(""),
    user: str = Depends(require_login),
    session: Session = Depends(get_session),
):
    _set_config(session, "webhook_url", webhook_url.strip())
    return RedirectResponse("/settings", status_code=303)


@router.post("/password")
def settings_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password2: str = Form(...),
    user: str = Depends(require_login),
    session: Session = Depends(get_session),
):
    username = current_username(request)
    db_user = session.exec(select(User).where(User.username == username)).first()
    error = None
    if not db_user or not verify_password(current_password, db_user.password_hash):
        error = "Current password is incorrect."
    elif new_password != new_password2 or len(new_password) < 8:
        error = "New passwords must match and be at least 8 characters."
    else:
        db_user.password_hash = hash_password(new_password)
        session.add(db_user)
        session.commit()

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "webhook_url": _get_config(session, "webhook_url"),
            "browse_roots": list(BROWSE_ROOTS.keys()),
            "password_error": error,
            "password_success": error is None,
        },
    )


@router.get("/verify")
def settings_verify(
    request: Request,
    root: str = "",
    path: str = "",
    token: str = "",
    user: str = Depends(require_login),
):
    if not (root and path) and not token:
        return templates.TemplateResponse(
            request, "verify_result.html", {"browse_roots": list(BROWSE_ROOTS.keys())}
        )
    try:
        src = _resolve_source(root, path, token)
        result = archive.verify_archive(src)
    except (ValueError, FileNotFoundError) as exc:
        result = {"integrity_ok": False, "stderr": str(exc)}
    return templates.TemplateResponse(
        request,
        "verify_result.html",
        {"result": result, "browse_roots": list(BROWSE_ROOTS.keys())},
    )
