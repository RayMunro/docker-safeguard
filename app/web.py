from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from .paths import human_size

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _inject_user(request: Request) -> dict:
    # Makes `user` available in every template automatically, so a route
    # can't forget to pass it and silently render the logged-out layout.
    return {"user": request.session.get("user")}


templates = Jinja2Templates(directory=str(TEMPLATES_DIR), context_processors=[_inject_user])
templates.env.filters["human_size"] = human_size
