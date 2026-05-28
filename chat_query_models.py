from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserLogQuery:
    username: str | None  # None = все участники из базы
    period: str  # today | yesterday | day_before | 24h
    hour_from: int | None = None
    hour_to: int | None = None
    minute_from: int = 0
    minute_to: int = 59
    phrase: str | None = None
    when_only: bool = False  # «во сколько сказал …»
