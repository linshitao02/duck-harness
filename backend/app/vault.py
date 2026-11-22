"""设置里的密钥入库前加密，读出来再用时解密。"""

import base64
import hashlib
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import ROOT_DIR, sqlite_file

logger = logging.getLogger("harness.vault")
PREFIX = "enc:v1:"


def _key_path() -> Path:
    return sqlite_file().parent / "secret.key"


def _fernet() -> Fernet:
    raw = os.environ.get("HARNESS_SECRET_KEY", "").strip()
    if raw:
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))
    path = _key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        key = path.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        path.write_bytes(key)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return Fernet(key)


def sealed(value: str | None) -> bool:
    return bool(value) and str(value).startswith(PREFIX)


def seal(value: str | None) -> str:
    text = (value or "").strip()
    if not text or sealed(text):
        return text
    token = _fernet().encrypt(text.encode("utf-8")).decode("ascii")
    return PREFIX + token


def reveal(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if not sealed(text):
        return text
    try:
        return _fernet().decrypt(text[len(PREFIX) :].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning("有一条密钥无法解密，已跳过")
        return ""
