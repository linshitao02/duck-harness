import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
UPLOAD_DIR = Path(os.environ.get("HARNESS_UPLOAD_DIR") or (ROOT_DIR / "data" / "uploads"))


def sqlite_file() -> Path:
    raw = os.environ.get("HARNESS_SQLITE_PATH", "").strip()
    return Path(raw) if raw else (ROOT_DIR / "data" / "harness.sqlite")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
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
