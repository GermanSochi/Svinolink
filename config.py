from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
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
    webhook_path: str = Field(default="", alias="WEBHOOK_PATH")
    port: int = Field(default=8080, alias="PORT")

    triggers_file: Path = Field(default=BASE_DIR / "triggers.json", alias="TRIGGERS_FILE")
    data_dir: Path = Field(default=BASE_DIR / "data", alias="DATA_DIR")

    @property
    def admin_ids(self) -> set[int]:
        out: set[int] = set()
        for part in self.admin_ids_raw.split(","):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
        return out

    @property
    def admin_usernames(self) -> set[str]:
        return {
            x.strip().lower().lstrip("@")
            for x in self.admin_usernames_raw.split(",")
            if x.strip()
        }


settings = Settings()
