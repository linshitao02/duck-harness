import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_root() -> Path:
    """数据目录。打包后放在 exe 旁边，源码运行时是项目根目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_root() -> Path:
    """只读资源。打包后在解压目录，源码运行时是项目根目录。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return install_root()


ROOT_DIR = install_root()
RESOURCE_DIR = resource_root()
BACKEND_DIR = RESOURCE_DIR / "backend" if is_frozen() else Path(__file__).resolve().parents[1]
ENV_FILE = ROOT_DIR / "backend" / ".env"
UPLOAD_DIR = Path(os.environ.get("HARNESS_UPLOAD_DIR") or (ROOT_DIR / "data" / "uploads"))


def sqlite_file() -> Path:
    raw = os.environ.get("HARNESS_SQLITE_PATH", "").strip()
    return Path(raw) if raw else (ROOT_DIR / "data" / "harness.sqlite")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE) if ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    ollama_base_url: str = "http://127.0.0.1:11434"
    auto_seed: bool = False
    enable_scheduler: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
