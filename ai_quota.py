from __future__ import annotations

import time
from collections import defaultdict

HOURLY_LIMIT = 30
WINDOW_SECONDS = 3600

_timestamps: dict[int, list[float]] = defaultdict(list)


def _prune(user_id: int, now: float | None = None) -> None:
    now = now or time.time()
    cutoff = now - WINDOW_SECONDS
    _timestamps[user_id] = [t for t in _timestamps[user_id] if t > cutoff]


def used_in_window(user_id: int) -> int:
    _prune(user_id)
    return len(_timestamps[user_id])


def remaining(user_id: int) -> int:
    return max(0, HOURLY_LIMIT - used_in_window(user_id))


def can_ask(user_id: int) -> bool:
    return used_in_window(user_id) < HOURLY_LIMIT


def record(user_id: int) -> None:
    now = time.time()
    _prune(user_id, now)
    _timestamps[user_id].append(now)


def reset_user(user_id: int) -> None:
    _timestamps.pop(user_id, None)
