from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str
    password_hash: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AppSetting(SQLModel, table=True):
    """Per-container backup preferences, keyed by container name."""

    container_name: str = Field(primary_key=True)
    scheduled: bool = False
    stop_before_backup: bool = True
    exclude_patterns: str = ""  # newline separated glob patterns
    included_extra_paths: str = ""  # newline separated non-appdata host paths opted in
    encrypt: bool = False
    encrypt_passphrase_hash: Optional[str] = None
    last_backup_at: Optional[datetime] = None
    last_backup_status: Optional[str] = None
    last_backup_path: Optional[str] = None


class BackupJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_uuid: str = Field(index=True)
    container_name: str
    kind: str = "backup"  # backup | restore
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    status: str = "running"  # running | success | failed
    archive_path: Optional[str] = None
    size_bytes: Optional[int] = None
    message: Optional[str] = None


class Schedule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    container_name: str
    cron: str
    destination: str
    retention: int = 5
    enabled: bool = True


class AppConfig(SQLModel, table=True):
    """Free-form key/value settings (webhook URL, theme, etc.)."""

    key: str = Field(primary_key=True)
    value: str
