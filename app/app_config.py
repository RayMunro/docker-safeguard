"""Small global key/value settings store (webhook URL, last-used folders,
etc.), backed by the AppConfig table. Shared across routers so there's one
place that knows how to read/write it."""

from __future__ import annotations

from .db import session_scope
from .models import AppConfig


def get_config(key: str, default: str = "") -> str:
    with session_scope() as session:
        row = session.get(AppConfig, key)
        return row.value if row else default


def set_config(key: str, value: str) -> None:
    with session_scope() as session:
        row = session.get(AppConfig, key)
        if row:
            row.value = value
        else:
            row = AppConfig(key=key, value=value)
        session.add(row)
        session.commit()
