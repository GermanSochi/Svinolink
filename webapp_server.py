from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from aiohttp import web

from config import settings
from deps import store
from miniapp_auth import parse_init_data, parse_user_session
from store import TriggerRule
from bot_stats import bot_stats

logger = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static" / "miniapp"
DASHBOARD_STATIC = Path(__file__).resolve().parent / "static" / "dashboard"


def _init_data_from_request(request: web.Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("tma "):
        return auth[4:].strip()
    return request.headers.get("X-Telegram-Init-Data", "") or request.query.get(
        "initData", ""
    )


def _rules_from_payload(items: list[dict]) -> list[TriggerRule]:
    rules: list[TriggerRule] = []
    for i, item in enumerate(items):
        words_raw = item.get("words") or item.get("word") or ""
        if isinstance(words_raw, list):
            words = [str(w).lower().strip() for w in words_raw if str(w).strip()]
        else:
            words = [w.strip() for w in str(words_raw).split(",") if w.strip()]
        response = str(item.get("response", "")).strip()
        if not words or not response:
            continue
        safe = "".join(c for c in words[0] if c.isalnum())[:12] or "w"
        uid = item.get("added_by_user_id")
        rules.append(
            TriggerRule(
                id=str(item.get("id") or f"t-{int(time.time())}-{i}-{safe}"),
                words=[w.lower() for w in words],
                response=response,
                once_per_day=bool(item.get("once_per_day", False)),
                match=str(item.get("match") or "exact"),
                added_by_user_id=int(uid) if uid is not None else None,
                added_by_username=item.get("added_by_username"),
            )
        )
    return rules


def _merge_authorship(
    chat_id: int, rules: list[TriggerRule], session
) -> list[TriggerRule]:
    old = {r.id: r for r in store.load_custom(chat_id)}
    merged: list[TriggerRule] = []
    for rule in rules:
        prev = old.get(rule.id)
        if prev and (prev.added_by_user_id or prev.added_by_username):
            rule.added_by_user_id = prev.added_by_user_id
            rule.added_by_username = prev.added_by_username
        else:
            rule.added_by_user_id = session.user_id
            rule.added_by_username = session.username
        merged.append(rule)
    return merged


async def miniapp_index(_: web.Request) -> web.Response:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return web.Response(
        text=html,
        content_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


async def api_get_triggers(request: web.Request) -> web.Response:
    try:
        init_raw = _init_data_from_request(request)
        fb = request.query.get("chat_id")
        fallback = None
        if fb is not None and str(fb).lstrip("-").isdigit():
            fallback = int(fb)
            if fallback >= 0:
                raise ValueError(
                    "нужен ID группы (отрицательное число, например -1001234567890)"
                )
        session = parse_init_data(init_raw, fallback_chat_id=fallback)
        if not store.get_chat_title(session.chat_id):
            store.register_chat(session.chat_id, title=f"Чат {session.chat_id}")
        defaults = store.load_defaults()
        custom = store.load_custom(session.chat_id)
        return web.json_response(
            {
                "chat_id": session.chat_id,
                "title": store.get_chat_title(session.chat_id),
                "builtin": [
                    {
                        "words": r.words,
                        "response": r.response,
                        "once_per_day": r.once_per_day,
                        "match": r.match,
                    }
                    for r in defaults
                ],
                "custom": [
                    {
                        "id": r.id,
                        "words": r.words,
                        "response": r.response,
                        "once_per_day": r.once_per_day,
                        "match": r.match,
                        "added_by_user_id": r.added_by_user_id,
                        "added_by_username": r.added_by_username,
                    }
                    for r in custom
                ],
            }
        )
    except Exception as exc:
        logger.warning("api_get_triggers: %s", exc)
        return web.json_response({"error": str(exc)}, status=401)


async def api_list_chats(request: web.Request) -> web.Response:
    try:
        init_raw = _init_data_from_request(request)
        parse_user_session(init_raw)
        chats = store.list_active_chats()
        payload = []
        defaults = store.load_defaults()
        for chat in chats:
            cid = chat["chat_id"]
            custom = store.load_custom(cid)
            payload.append(
                {
                    "chat_id": cid,
                    "title": chat["title"] or f"Чат {cid}",
                    "builtin_count": len(defaults),
                    "custom_count": len(custom),
                    "added_by_user_id": chat.get("added_by_user_id"),
                }
            )
        return web.json_response({"chats": payload})
    except Exception as exc:
        logger.warning("api_list_chats: %s", exc)
        return web.json_response({"error": str(exc)}, status=401)


async def api_delete_chat(request: web.Request) -> web.Response:
    try:
        init_raw = _init_data_from_request(request)
        parse_user_session(init_raw)
        body = await request.json()
        cid = body.get("chat_id")
        if cid is None:
            raise ValueError("не указан chat_id")
        chat_id = int(cid)
        if chat_id >= 0:
            raise ValueError("нужен ID группы (отрицательное число)")
        store.remove_chat_from_miniapp(chat_id)
        return web.json_response({"ok": True, "chat_id": chat_id})
    except Exception as exc:
        logger.warning("api_delete_chat: %s", exc)
        return web.json_response({"error": str(exc)}, status=400)


async def api_save_triggers(request: web.Request) -> web.Response:
    try:
        init_raw = _init_data_from_request(request)
        body = await request.json()
        fb = body.get("chat_id")
        if fb is None:
            raise ValueError("не указан chat_id группы")
        fallback = int(fb)
        if fallback >= 0:
            raise ValueError(
                "нужен ID группы (отрицательное число, например -1001234567890)"
            )
        session = parse_init_data(init_raw, fallback_chat_id=fallback)
        if not store.get_chat_title(session.chat_id):
            store.register_chat(session.chat_id, title=f"Чат {session.chat_id}")
        items = body.get("triggers") or body.get("custom") or []
        if not isinstance(items, list):
            raise ValueError("неверный формат")
        rules = _rules_from_payload(items)
        rules = _merge_authorship(session.chat_id, rules, session)
        store.save_custom(session.chat_id, rules)
        return web.json_response(
            {
                "ok": True,
                "saved": len(rules),
                "chat_id": session.chat_id,
                "message": "Сохранено для всех в этом чате",
            }
        )
    except Exception as exc:
        logger.warning("api_save_triggers: %s", exc)
        return web.json_response({"error": str(exc)}, status=400)


def register_miniapp_routes(app: web.Application) -> None:
    app.router.add_get("/miniapp", miniapp_index)
    app.router.add_get("/miniapp/", miniapp_index)
    app.router.add_get("/api/triggers", api_get_triggers)
    app.router.add_get("/api/chats", api_list_chats)
    app.router.add_delete("/api/chats", api_delete_chat)
    app.router.add_post("/api/triggers", api_save_triggers)

    # Dashboard
    app.router.add_get("/dashboard", dashboard_index)
    app.router.add_get("/dashboard/", dashboard_index)
    app.router.add_get("/api/dashboard", api_dashboard)


async def dashboard_index(_: web.Request) -> web.Response:
    html = (DASHBOARD_STATIC / "index.html").read_text(encoding="utf-8")
    return web.Response(
        text=html,
        content_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


_miniapp_manual_input: bool | None = None


def _check_miniapp_manual_input() -> bool:
    global _miniapp_manual_input
    if _miniapp_manual_input is None:
        try:
            html = (STATIC / "index.html").read_text(encoding="utf-8")
            _miniapp_manual_input = "manualChatId" in html
        except OSError:
            _miniapp_manual_input = False
    return _miniapp_manual_input


async def api_dashboard(_: web.Request) -> web.Response:
    from instagram_download import _client, _cookies_loaded, _ready
    from chat_memory import is_pool_ready

    stats = bot_stats.snapshot()
    ig_active = settings.instagram_is_active()
    ig_paused = settings.instagram_paused
    ig_user_id = None
    if _client is not None and _client.user_id is not None:
        ig_user_id = str(_client.user_id)

    payload = {
        "status": "ok",
        "mode": "webhook" if settings.webhook_base_url.strip() else "polling",
        "version": settings.app_version,
        "chat_memory": (
            "connected"
            if is_pool_ready()
            else ("configured" if settings.supabase_database_url.strip() else "off")
        ),
        "instagram": {
            "active": ig_active,
            "paused": ig_paused,
            "cookies_loaded": _cookies_loaded,
            "client_ready": _ready,
            "user_id": ig_user_id,
            "last_download": stats.get("last_download"),
        },
        "stats": stats,
    }
    return web.json_response(payload)
