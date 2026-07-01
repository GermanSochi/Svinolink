"""Внутренняя статистика бота для дашборда."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class DownloadStat:
    url: str
    ok: bool
    method: str
    size: int
    elapsed_ms: int
    ts: float
    error: str = ""


@dataclass
class BotStats:
    started_at: float = field(default_factory=time.time)
    messages_processed: int = 0
    instagram_attempts: int = 0
    instagram_success: int = 0
    instagram_fail: int = 0
    instagram_bytes: int = 0
    last_download: DownloadStat | None = None
    recent_downloads: list[DownloadStat] = field(default_factory=list)
    recent_errors: list[str] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def record_download(self, stat: DownloadStat) -> None:
        with self._lock:
            self.instagram_attempts += 1
            if stat.ok:
                self.instagram_success += 1
                self.instagram_bytes += stat.size
            else:
                self.instagram_fail += 1
            self.last_download = stat
            self.recent_downloads.append(stat)
            if len(self.recent_downloads) > 20:
                self.recent_downloads = self.recent_downloads[-20:]

    def record_message(self) -> None:
        with self._lock:
            self.messages_processed += 1

    def record_error(self, msg: str) -> None:
        with self._lock:
            ts = time.strftime("%H:%M:%S")
            self.recent_errors.append(f"[{ts}] {msg}")
            if len(self.recent_errors) > 15:
                self.recent_errors = self.recent_errors[-15:]

    def snapshot(self) -> dict:
        with self._lock:
            uptime = int(time.time() - self.started_at)
            hours, rem = divmod(uptime, 3600)
            mins, secs = divmod(rem, 60)
            return {
                "uptime": f"{hours}ч {mins}м {secs}с",
                "uptime_seconds": uptime,
                "messages": self.messages_processed,
                "ig_attempts": self.instagram_attempts,
                "ig_success": self.instagram_success,
                "ig_fail": self.instagram_fail,
                "ig_bytes": self.instagram_bytes,
                "ig_bytes_human": _human_bytes(self.instagram_bytes),
                "last_download": _dl_to_dict(self.last_download),
                "recent_downloads": [_dl_to_dict(d) for d in self.recent_downloads[-10:]],
                "recent_errors": list(self.recent_errors),
            }


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _dl_to_dict(s: DownloadStat | None) -> dict | None:
    if not s:
        return None
    return {
        "url": s.url,
        "ok": s.ok,
        "method": s.method,
        "size": s.size,
        "size_human": _human_bytes(s.size),
        "elapsed_ms": s.elapsed_ms,
        "error": s.error,
        "ts": time.strftime("%H:%M:%S", time.localtime(s.ts)),
    }


bot_stats = BotStats()
