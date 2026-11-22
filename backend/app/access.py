"""当前登录用户，以及别的用户的目录边界。"""

import os
from contextvars import ContextVar
from pathlib import Path

from sqlalchemy import select

from .config import UPLOAD_DIR, sqlite_file

_actor: ContextVar[int | None] = ContextVar("harness_actor", default=None)
_protected: ContextVar[tuple[str, ...]] = ContextVar("harness_protected", default=())


def current_user_id() -> int | None:
    return _actor.get()


def owner_kwargs() -> dict:
    uid = current_user_id()
    return {"user_id": uid} if uid else {}


def protected_paths() -> tuple[str, ...]:
    return _protected.get()


def set_actor(user_id: int | None, protected: tuple[str, ...] = ()) -> tuple:
    return _actor.set(user_id), _protected.set(protected)


def reset_actor(tokens: tuple) -> None:
    _actor.reset(tokens[0])
    _protected.reset(tokens[1])


def clear_actor() -> None:
    _actor.set(None)
    _protected.set(())


def accounts_root() -> Path:
    raw = os.environ.get("HARNESS_ACCOUNTS_DIR", "").strip()
    if raw:
        return Path(raw)
    return sqlite_file().parent / "workspaces"


def private_workspace(user_id: int) -> str:
    path = accounts_root() / str(int(user_id))
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _norm(path: Path) -> str:
    try:
        resolved = str(path.resolve()).lower()
    except OSError:
        resolved = str(path).lower()
    return resolved.replace("/", "\\")


def protected_for(db, user_id: int) -> tuple[str, ...]:
    from .models import Conversation, User, UserSetting

    found: list[str] = []
    accounts = accounts_root()
    other_ids = db.scalars(select(User.id).where(User.id != user_id)).all()
    for other in other_ids:
        found.append(_norm(accounts / str(other)))
        found.append(_norm(UPLOAD_DIR / f"u{int(other)}"))
        found.append(f"workspaces\\{int(other)}")
        found.append(f"uploads\\u{int(other)}")
    for row in db.scalars(
        select(UserSetting).where(UserSetting.user_id != user_id, UserSetting.name == "workspace")
    ).all():
        if row.value:
            found.append(_norm(Path(row.value)))
    for raw in db.scalars(
        select(Conversation.workspace).where(Conversation.user_id.is_not(None), Conversation.user_id != user_id)
    ).all():
        if raw:
            found.append(_norm(Path(raw)))
    unique = []
    for item in found:
        if item and item not in unique:
            unique.append(item)
    return tuple(unique)


def path_is_foreign(path: Path) -> bool:
    target = _norm(path)
    for root in _protected.get():
        if not root:
            continue
        if "\\" not in root[1:]:
            continue
        if target == root or target.startswith(root + "\\"):
            return True
    return False


def command_touches_foreign(command: str) -> str | None:
    text = (command or "").lower().replace("/", "\\")
    for root in _protected.get():
        if root and root in text:
            return "拒绝访问其他用户的目录"
    return None


def workspace_allowed(db, user_id: int, raw: str) -> str:
    path = Path(raw)
    if not path.is_dir():
        raise ValueError("工作区目录不存在")
    resolved = path.resolve()
    target = _norm(resolved)
    for root in protected_for(db, user_id):
        if "\\" not in root[1:]:
            continue
        if target == root or target.startswith(root + "\\") or root.startswith(target + "\\"):
            raise ValueError("这个目录属于别的用户")
    return str(resolved)
