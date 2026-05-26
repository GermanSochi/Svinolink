from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(default="", alias="BOT_TOKEN")
    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")
    admin_usernames_raw: str = Field(default="gersochi", alias="ADMIN_USERNAMES")

    yandex_api_key: str = Field(default="", alias="YANDEX_API_KEY")
    yandex_folder_id: str = Field(default="b1g3l4knr91bsq8mqhaq", alias="YANDEX_FOLDER_ID")
    yandex_model: str = Field(default="yandexgpt-lite", alias="YANDEX_MODEL")

    webhook_base_url: str = Field(default="", alias="WEBHOOK_BASE_URL")
    public_base_url: str = Field(default="", alias="PUBLIC_BASE_URL")
    webhook_path: str = Field(default="webhook", alias="WEBHOOK_PATH")
    port: int = Field(default=8080, alias="PORT")

    triggers_file: Path = Field(default=BASE_DIR / "triggers.json", alias="TRIGGERS_FILE")
    data_dir: Path = Field(default=BASE_DIR / "data", alias="DATA_DIR")
    downloads_dir: Path = Field(default=BASE_DIR / "downloads", alias="DOWNLOADS_DIR")

    instagram_username: str = Field(default="", alias="INSTAGRAM_USERNAME")
    instagram_password: str = Field(default="", alias="INSTAGRAM_PASSWORD")
    instagram_session_file: Path = Field(
        default=BASE_DIR / "data" / "instagram_session.json",
        alias="INSTAGRAM_SESSION_FILE",
    )
    instagram_cookies_file: Path = Field(
        default=BASE_DIR / "data" / "cookies.txt",
        alias="INSTAGRAM_COOKIES_FILE",
    )
    supabase_database_url: str = Field(default="", alias="SUPABASE_DATABASE_URL")

    @field_validator("supabase_database_url", mode="before")
    @classmethod
    def normalize_supabase_url(cls, v: object) -> object:
        if not isinstance(v, str):
            return v
        url = v.strip()
        if len(url) >= 2 and url[0] == url[-1] and url[0] in "\"'":
            url = url[1:-1].strip()
        return url

    @field_validator("webhook_base_url", "public_base_url", mode="before")
    @classmethod
    def strip_trailing_slash(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().rstrip("/")
        return v

    @property
    def admin_ids(self) -> set[int]:
        out: set[int] = set()
        for part in self.admin_ids_raw.split(","):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
        return out

    @property
    def app_base_url(self) -> str:
        return (self.public_base_url or self.webhook_base_url).rstrip("/")

    @property
    def app_version(self) -> str:
        env = os.getenv("RENDER_GIT_COMMIT", "").strip()
        if env:
            return env[:12]
        version_file = BASE_DIR / "VERSION"
        if version_file.is_file():
            return version_file.read_text(encoding="utf-8").strip() or "dev"
        return "dev"

    @property
    def miniapp_url(self) -> str:
        base = self.app_base_url
        if not base:
            return ""
        return f"{base}/miniapp?v={self.app_version}"

    @property
    def webhook_route(self) -> str:
        path = (self.webhook_path or "webhook").strip().lstrip("/")
        return f"/{path}"

    @property
    def webhook_full_url(self) -> str:
        base = self.webhook_base_url.rstrip("/")
        if not base:
            return ""
        return f"{base}{self.webhook_route}"

    @property
    def is_render(self) -> bool:
        return os.getenv("RENDER", "").lower() in {"true", "1", "yes"}

    @property
    def admin_usernames(self) -> set[str]:
        return {
            x.strip().lower().lstrip("@")
            for x in self.admin_usernames_raw.split(",")
            if x.strip()
        }


settings = Settings()
