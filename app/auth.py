from fastapi import HTTPException, Request
from passlib.context import CryptContext
from sqlmodel import select

from .db import session_scope
from .models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def any_user_exists() -> bool:
    with session_scope() as session:
        return session.exec(select(User)).first() is not None


def get_user_by_username(username: str) -> User | None:
    with session_scope() as session:
        return session.exec(select(User).where(User.username == username)).first()


def create_user(username: str, password: str) -> User:
    with session_scope() as session:
        user = User(username=username, password_hash=hash_password(password))
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def current_username(request: Request) -> str | None:
    return request.session.get("user")


def require_login(request: Request) -> str:
    """Dependency: returns the logged-in username, or redirects to /login."""
    if not any_user_exists():
        raise HTTPException(status_code=303, headers={"Location": "/setup"})
    username = current_username(request)
    if not username:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return username
