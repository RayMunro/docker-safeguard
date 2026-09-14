from sqlmodel import SQLModel, create_engine, Session

from .config import DB_PATH

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session


def session_scope() -> Session:
    """For use outside of FastAPI's dependency injection (background threads)."""
    return Session(engine)
