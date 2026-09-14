import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import get_secret_key
from .db import init_db
from .routers import auth, backup, browse, dashboard, logs, restore, schedule, settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        from .scheduler import start_scheduler

        start_scheduler()
    except Exception:  # noqa: BLE001
        logging.getLogger("docker_safeguard").exception("scheduler failed to start")
    yield


app = FastAPI(title="Docker Safeguard", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=get_secret_key(), same_site="lax")

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(backup.router)
app.include_router(restore.router)
app.include_router(browse.router)
app.include_router(schedule.router)
app.include_router(settings.router)
app.include_router(logs.router)
