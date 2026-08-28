# Graph Report - Svinolink  (2026-05-30)

## Corpus Check
- 71 files · ~89,643 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 715 nodes · 1762 edges · 28 communities (25 shown, 3 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 102 edges (avg confidence: 0.51)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `d90d2c02`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]

## God Nodes (most connected - your core abstractions)
1. `TriggerStore` - 39 edges
2. `str` - 33 edges
3. `handle_svin_ai()` - 27 edges
4. `_connect_once()` - 25 edges
5. `is_memory_enabled()` - 25 edges
6. `TriggerRule` - 24 edges
7. `EconomyBalance` - 22 edges
8. `int` - 22 edges
9. `str` - 20 edges
10. `database_url()` - 20 edges

## Surprising Connections (you probably didn't know these)
- `bool` --uses--> `TriggerRule`  [INFERRED]
  trigger_supabase.py → store.py
- `Application` --uses--> `TriggerRule`  [INFERRED]
  webapp_server.py → store.py
- `int` --uses--> `TriggerRule`  [INFERRED]
  webapp_server.py → store.py
- `str` --uses--> `TriggerRule`  [INFERRED]
  webapp_server.py → store.py
- `UserLogQuery` --uses--> `UserLogQuery`  [INFERRED]
  chat_queries.py → chat_query_models.py

## Communities (28 total, 3 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (89): Bot, Any, bool, Connection, int, str, Any, bool (+81 more)

### Community 1 - "Community 1"
Cohesion: 0.08
Nodes (61): bool, str, UserLogQuery, int, str, int, object, str (+53 more)

### Community 2 - "Community 2"
Cohesion: 0.09
Nodes (24): bool, Connection, int, Path, str, Нумерация только пользовательских (не встроенных)., Убрать группу из Mini App и удалить все кастомные триггеры., Текстовая выжимка триггеров для промпта GPT. (+16 more)

### Community 3 - "Community 3"
Cohesion: 0.08
Nodes (53): BaseFilter, Exception, str, bool, str, bool, Bot, Message (+45 more)

### Community 4 - "Community 4"
Cohesion: 0.09
Nodes (59): build_search_evidence(), build_snippet_fallback(), build_wiki_fallback(), _clean_query(), ddg_search_query(), download_image_bytes(), extract_http_url(), extract_knowledge_query() (+51 more)

### Community 5 - "Community 5"
Cohesion: 0.10
Nodes (46): AsyncSession, DeclarativeBase, str, apply_economy_action(), Any, float, int, str (+38 more)

### Community 6 - "Community 6"
Cohesion: 0.08
Nodes (49): Path, str, bool, Exception, Path, RuntimeError, str, bool (+41 more)

### Community 7 - "Community 7"
Cohesion: 0.11
Nodes (27): bool, int, str, Message, str, bool, float, int (+19 more)

### Community 8 - "Community 8"
Cohesion: 0.14
Nodes (26): Bot, Message, str, cmd_docx(), cmd_pdf(), cmd_scrape(), cmd_tts(), cmd_xlsx() (+18 more)

### Community 9 - "Community 9"
Cohesion: 0.19
Nodes (22): Connection, int, str, bool, Message, str, ChatPersonality, _clamp() (+14 more)

### Community 10 - "Community 10"
Cohesion: 0.21
Nodes (13): bool, int, str, GameStore, bool, int, str, GameStore (+5 more)

### Community 11 - "Community 11"
Cohesion: 0.19
Nodes (17): int, str, int, str, date, datetime, chat_examples_html(), chat_examples_markdown() (+9 more)

### Community 12 - "Community 12"
Cohesion: 0.15
Nodes (7): BaseSettings, bool, int, object, str, Скачивание IG включено только без паузы и с cookies/сессией/логином., Settings

### Community 13 - "Community 13"
Cohesion: 0.23
Nodes (18): int, str, Request, Response, parse_init_data(), parse_user_session(), WebAppSession, api_delete_chat() (+10 more)

### Community 14 - "Community 14"
Cohesion: 0.21
Nodes (16): FreeTypeFont, Image, ImageDraw, ImageFont, bytes, int, Path, str (+8 more)

### Community 15 - "Community 15"
Cohesion: 0.35
Nodes (10): BaseMiddleware, Bot, Dispatcher, Message, close_chat_memory(), _build_dispatcher(), cmd_start(), handle_triggers() (+2 more)

### Community 16 - "Community 16"
Cohesion: 0.20
Nodes (9): Быстрый мини-тест (3 минуты), Вернуть как было (пример), Подготовка, Проверка юмора и токсичности Свина, Режим А — «Справочник» (юмор 10, подкол 10), Режим Б — «Острый» (юмор 95, подкол 95), Режим В — только юмор (юмор 90, подкол 10), Режим Г — только подкол (юмор 10, подкол 90) (+1 more)

### Community 17 - "Community 17"
Cohesion: 0.22
Nodes (8): 24/7 без ПК (webhook), Mini App «⚙️ Тригеры» (в группе), Render (24/7, ПК можно выключить), Svinolink, Аватарка бота, Важные правила, Локальный запуск (polling), Триггеры в группе (настраиваются)

### Community 18 - "Community 18"
Cohesion: 0.42
Nodes (8): _coerce_router_result(), _extract_json(), Единственная точка входа: отправляем текст в Yandex GPT,     получаем JSON, пар, route_intent(), RouterResult, TypedDict, Any, str

### Community 19 - "Community 19"
Cohesion: 0.33
Nodes (5): ChatMemberUpdated, bot_joined(), register_from_forward(), Bot, Message

### Community 20 - "Community 20"
Cohesion: 0.40
Nodes (4): Checkpoint: рабочая версия Svinolink, Env на Render, После этого checkpoint, Что работает на этом снимке

### Community 21 - "Community 21"
Cohesion: 0.40
Nodes (4): Render (чеклист), Svinolink — пауза Instagram, Когда захочешь снова видео из Instagram, Что сделано

### Community 22 - "Community 22"
Cohesion: 0.50
Nodes (3): int, str, miniapp_url_for_chat()

### Community 23 - "Community 23"
Cohesion: 0.67
Nodes (3): str, parse_meme_request(), parse_video_request()

## Knowledge Gaps
- **79 isolated node(s):** `int`, `str`, `bool`, `InlineKeyboardMarkup`, `str` (+74 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `is_memory_enabled()` connect `Community 0` to `Community 1`, `Community 2`, `Community 11`?**
  _High betweenness centrality (0.139) - this node is a cross-community bridge._
- **Why does `handle_svin_ai()` connect `Community 3` to `Community 1`, `Community 4`, `Community 5`, `Community 8`, `Community 9`, `Community 11`, `Community 15`, `Community 18`?**
  _High betweenness centrality (0.131) - this node is a cross-community bridge._
- **Why does `try_web_search_reply()` connect `Community 4` to `Community 3`?**
  _High betweenness centrality (0.119) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `TriggerStore` (e.g. with `int` and `str`) actually correct?**
  _`TriggerStore` has 4 INFERRED edges - model-reasoned connections that need verification._
- **What connects `int`, `str`, `bool` to the rest of the system?**
  _163 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.05713058419243986 - nodes in this community are weakly interconnected._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.07594381035996488 - nodes in this community are weakly interconnected._