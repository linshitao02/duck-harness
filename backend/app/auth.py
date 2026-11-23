"""用户名密码和登录会话。密钥只存哈希，会话放在 HttpOnly Cookie 里。"""

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, update

from .access import accounts_root
from .models import AuthSession, Conversation, McpServer, Memory, Schedule, Skill, Task, UsageEvent, User

COOKIE = "duck_session"


def session_token_from_request(request) -> str:
    """网页用 Cookie。小程序读不到 HttpOnly Cookie，改带 Authorization: Bearer。"""
    cookie = request.cookies.get(COOKIE, "") if getattr(request, "cookies", None) else ""
    if cookie:
        return cookie
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return (request.headers.get("x-duck-session") or "").strip()
REMEMBER_DAYS = 30
SESSION_HOURS = 12
_USERNAME = re.compile(r"^[\w\u4e00-\u9fff]{2,32}$")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(rounds),
        )
        return hmac.compare_digest(got, bytes.fromhex(digest_hex))
    except (TypeError, ValueError):
        return False


def public_user(user: User) -> dict:
    return {"id": user.id, "name": user.name or user.username or "用户", "username": user.username or ""}


def _clean_username(username: str) -> str:
    text = (username or "").strip()
    if not _USERNAME.match(text):
        raise ValueError("用户名用 2 到 32 位字母、数字或中文")
    return text


def _clean_password(password: str) -> str:
    text = password or ""
    if len(text) < 6 or len(text) > 72:
        raise ValueError("密码需要 6 到 72 位")
    return text


def _claim_orphans(db, user: User) -> None:
    for model in (Conversation, Memory, Task, Schedule, UsageEvent, Skill, McpServer):
        db.execute(update(model).where(model.user_id.is_(None)).values(user_id=user.id))


def register_user(db, username: str, password: str, name: str = "") -> User:
    username = _clean_username(username)
    password = _clean_password(password)
    display = (name or "").strip()[:64] or username
    existing = db.scalar(select(User).where(User.username == username))
    if existing is not None:
        raise ValueError("这个用户名已经注册过")
    named = db.scalar(
        select(func.count()).select_from(User).where(User.username.is_not(None), User.username != "")
    ) or 0
    ghost = db.scalar(
        select(User).where(or_(User.username.is_(None), User.username == "")).order_by(User.id.asc())
    )
    if ghost is not None and named == 0:
        ghost.username = username
        ghost.password_hash = hash_password(password)
        ghost.name = display
        _claim_orphans(db, ghost)
        db.commit()
        db.refresh(ghost)
        return ghost
    user = User(name=display, username=username, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    accounts_root()
    return user


def login_user(db, username: str, password: str) -> User | None:
    username = (username or "").strip()
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not user.password_hash:
        verify_password(password or "", "pbkdf2_sha256$200000$" + "00" * 16 + "$" + "00" * 32)
        return None
    if not verify_password(password or "", user.password_hash):
        return None
    return user


def issue_session(db, user: User, remember: bool) -> AuthSession:
    now = datetime.now()
    row = AuthSession(
        token=secrets.token_urlsafe(32),
        user_id=user.id,
        remember=bool(remember),
        expires_at=now + (timedelta(days=REMEMBER_DAYS) if remember else timedelta(hours=SESSION_HOURS)),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def read_session(db, token: str) -> tuple[User, AuthSession] | None:
    if not token:
        return None
    row = db.get(AuthSession, token)
    if row is None:
        return None
    if row.expires_at <= datetime.now():
        db.delete(row)
        db.commit()
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.username:
        return None
    return user, row


def drop_session(db, token: str) -> None:
    row = db.get(AuthSession, token) if token else None
    if row is not None:
        db.delete(row)
        db.commit()
