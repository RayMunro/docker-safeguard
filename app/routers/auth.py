from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from ..auth import any_user_exists, create_user, get_user_by_username, verify_password
from ..web import templates

router = APIRouter()


@router.get("/setup")
def setup_form(request: Request):
    if any_user_exists():
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "setup.html", {})


@router.post("/setup")
def setup_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
):
    if any_user_exists():
        return RedirectResponse("/login", status_code=303)
    if password != password2 or len(password) < 8:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"error": "Passwords must match and be at least 8 characters."},
        )
    create_user(username.strip(), password)
    request.session["user"] = username.strip()
    return RedirectResponse("/", status_code=303)


@router.get("/login")
def login_form(request: Request):
    if not any_user_exists():
        return RedirectResponse("/setup")
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    user = get_user_by_username(username.strip())
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Incorrect username or password."}
        )
    request.session["user"] = user.username
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
