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
    # true / 1 / yes — бот не трогает Instagram (ни cookies, ни логин)
    instagram_paused: bool = Field(default=False, alias="INSTAGRAM_PAUSED")

    # --- Feature flags (все по умолчанию выключены) ---
    ai_enabled: bool = Field(default=False, alias="AI_ENABLED")
    web_search_enabled: bool = Field(default=False, alias="WEB_SEARCH_ENABLED")
    games_enabled: bool = Field(default=False, alias="GAMES_ENABLED")
    memory_enabled: bool = Field(default=False, alias="MEMORY_ENABLED")
    skills_tools_enabled: bool = Field(default=False, alias="SKILLS_TOOLS_ENABLED")

    @field_validator("instagram_paused", mode="before")
    @classmethod
    def parse_instagram_paused(cls, v: object) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return True
        s = str(v).strip().lower()
        if s in {"", "0", "false", "no", "off", "resume", "run"}:
            return False
        return s in {"1", "true", "yes", "on", "pause", "paused"}
    supabase_database_url: str = Field(default="", alias="SUPABASE_DATABASE_URL")
    # Границы «сегодня/вчера/позавчера» для chat_history (Supabase в UTC).
    chat_timezone: str = Field(default="Europe/Moscow", alias="CHAT_TIMEZONE")

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

    _admin_ids_cache: set[int] | None = None

    @property
    def admin_ids(self) -> set[int]:
        if self._admin_ids_cache is not None:
            return self._admin_ids_cache
        out: set[int] = set()
        for part in self.admin_ids_raw.split(","):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
        self._admin_ids_cache = out
        return out

    @property
    def app_base_url(self) -> str:
        return (self.public_base_url or self.webhook_base_url).rstrip("/")

    _app_version_cache: str | None = None

    @property
    def app_version(self) -> str:
        if self._app_version_cache is not None:
            return self._app_version_cache
        env = os.getenv("RENDER_GIT_COMMIT", "").strip()
        if env:
            self._app_version_cache = env[:12]
        else:
            version_file = BASE_DIR / "VERSION"
            if version_file.is_file():
                self._app_version_cache = version_file.read_text(encoding="utf-8").strip() or "dev"
            else:
                self._app_version_cache = "dev"
        return self._app_version_cache

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

    def instagram_is_active(self) -> bool:
        """Скачивание IG включено только без паузы и с cookies/сессией/логином."""
        if self.instagram_paused:
            return False
        if self.instagram_cookies_file.is_file():
            return True
        if os.environ.get("INSTAGRAM_COOKIES_JSON", "").strip():
            return True
        if self.instagram_session_file.is_file():
            return True
        if self.instagram_username.strip() and self.instagram_password.strip():
            return True
        return False

    @property
    def admin_usernames(self) -> set[str]:
        return {
            x.strip().lower().lstrip("@")
            for x in self.admin_usernames_raw.split(",")
            if x.strip()
        }


settings = Settings()
