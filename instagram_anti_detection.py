"""Instagram anti-detection utilities.

Provides:
  - curl-impersonation for instagrapi (TLS fingerprint matching real Chrome)
  - Proxy pool rotation (round-robin + random jitter)
  - Smart random delays (Gumbel-distributed, looks human)
  - Device state rotation (randomize device fingerprint per-session)
"""
from __future__ import annotations

import logging
import math
import os
import random
import time
from itertools import cycle
from typing import Iterator

logger = logging.getLogger(__name__)

# ── curl-impersonation ──

CURL_IMPERSONATE = "chrome136"


def configure_impersonation(cl) -> None:
    """Set curl impersonation on instagrapi Client.

    Requires instagrapi>=2.18.18 with curl-cffi installed.
    Makes TLS fingerprint identical to real Chrome 136.
    """
    try:
        cl.impersonate = CURL_IMPERSONATE
        logger.info("anti_detect: impersonation=%s", CURL_IMPERSONATE)
    except Exception as exc:
        logger.warning("anti_detect: impersonation unavailable: %s", exc)


# ── Proxy rotation ──

class ProxyPool:
    """Round-robin proxy pool with jitter.

    Supports:
      - Single: PROXY_URL=socks5h://127.0.0.1:10808
      - Pool: PROXY_POOL=socks5h://host1:1080,socks5h://host2:1080
      - Legacy: PROXY_ENABLED=1 + PROXY_URL
    """

    def __init__(self) -> None:
        self._proxies: list[str] = []
        self._iterator: Iterator[str] | None = None
        self._last_used: float = 0.0

    def _load_proxies(self) -> list[str]:
        if self._proxies:
            return self._proxies
        pool_raw = os.environ.get("PROXY_POOL", "").strip()
        if pool_raw:
            self._proxies = [p.strip() for p in pool_raw.split(",") if p.strip()]
            if self._proxies:
                logger.info("anti_detect: proxy pool (%d proxies)", len(self._proxies))
                return self._proxies
        if os.environ.get("PROXY_ENABLED") == "1":
            url = os.environ.get("PROXY_URL", "socks5h://127.0.0.1:10808").strip()
            if url:
                self._proxies = [url]
                return self._proxies
        url = os.environ.get("PROXY_URL", "").strip()
        if url:
            self._proxies = [url]
            return self._proxies
        return []

    def _ensure_iterator(self) -> None:
        if self._iterator is None:
            proxies = self._load_proxies()
            if len(proxies) > 1:
                shuffled = list(proxies)
                random.shuffle(shuffled)
                self._iterator = cycle(shuffled)
            elif proxies:
                self._iterator = iter(proxies)

    def get(self) -> dict[str, str] | None:
        """Get next proxy in rotation. Returns requests-style dict or None."""
        self._ensure_iterator()
        if self._iterator is None:
            return None
        now = time.monotonic()
        elapsed = now - self._last_used
        if elapsed < 0.3:
            time.sleep(max(0, 0.3 + random.uniform(0, 0.5) - elapsed))
        proxy = next(self._iterator)
        self._last_used = time.monotonic()
        return {"http": proxy, "https": proxy}

    @property
    def active(self) -> bool:
        return bool(self._load_proxies())

    @property
    def count(self) -> int:
        return len(self._load_proxies())


proxy_pool = ProxyPool()


# ── Smart random delays ──

def human_delay(min_sec: float = 2.0, max_sec: float = 8.0) -> float:
    """Gumbel-distributed delay — right-skewed, looks natural.

    Most delays are short with occasional longer pauses.
    Beats uniform distribution which is detectable.
    """
    u = random.random()
    gumbel = -math.log(-math.log(max(u, 1e-10)))
    # Normalize Gumbel (~[-0.577, 2.97]) to [0, 1]
    normalized = max(0.0, min(1.0, (gumbel + 0.577) / 3.55))
    return min_sec + normalized * (max_sec - min_sec)


async def async_smart_sleep(min_sec: float = 2.0, max_sec: float = 8.0) -> None:
    """Async human-like sleep."""
    import asyncio
    await asyncio.sleep(human_delay(min_sec, max_sec))


def post_jitter(base_gap: float) -> float:
    """Natural jitter for inter-post delays. ±20% with bias toward longer."""
    return base_gap * (0.80 + random.random() * 0.40)


# ── Device state rotation ──

_DEVICES = [
    {"app_version": "334.0.0.30.90", "android_version": 34, "android_release": "14",
     "dpi": "440dpi", "resolution": "1080x2340", "manufacturer": "Samsung",
     "device": "SM-S928B", "model": "Galaxy S24 Ultra", "cpu": "SM8650",
     "version_code": "334000030"},
    {"app_version": "334.0.0.30.90", "android_version": 34, "android_release": "14",
     "dpi": "420dpi", "resolution": "1080x2400", "manufacturer": "Google",
     "device": "Pixel 8 Pro", "model": "Pixel 8 Pro", "cpu": "Tensor G3",
     "version_code": "334000030"},
    {"app_version": "334.0.0.30.90", "android_version": 14, "android_release": "14",
     "dpi": "440dpi", "resolution": "1080x2400", "manufacturer": "Xiaomi",
     "device": "2304FPN6DC", "model": "Xiaomi 14", "cpu": "SM8650",
     "version_code": "334000030"},
    {"app_version": "334.0.0.31.105", "device_model": "iPhone16,2",
     "os_version": "17.5.1", "locale": "en_US", "version_code": "334000031"},
    {"app_version": "334.0.0.31.105", "device_model": "iPhone15,4",
     "os_version": "17.6", "locale": "en_US", "version_code": "334000031"},
]


def apply_device_rotation(cl) -> None:
    """Randomize client device fingerprint per session."""
    device = random.choice(_DEVICES).copy()
    try:
        cl.set_device(device)
        logger.info("anti_detect: device=%s %s",
                     device.get("manufacturer", device.get("device_model", "?")),
                     device.get("device", "?"))
    except Exception as exc:
        logger.warning("anti_detect: device rotation failed: %s", exc)


# ── Timing helpers for multi-account scanning ──

def between_accounts_delay() -> float:
    """3-12s between scanning different IG accounts (heavy operation)."""
    return random.uniform(3.0, 12.0)


def between_requests_delay() -> float:
    """0.5-2.5s between sequential API calls."""
    return random.uniform(0.5, 2.5)

