from fastapi import APIRouter, Depends, Request

from ..auth import require_login
from ..paths import list_directory
from ..web import templates

router = APIRouter(prefix="/browse")


@router.get("/dirs")
def browse_dirs(
    request: Request,
    root: str = "shares",
    path: str = "",
    target_input: str = "destination_subpath",
    open_url: str = "",
    user: str = Depends(require_login),
):
    listing = list_directory(root, path)
    return templates.TemplateResponse(
        request,
        "partials/browser.html",
        {"listing": listing, "root": root, "target_input": target_input, "open_url": open_url},
    )
