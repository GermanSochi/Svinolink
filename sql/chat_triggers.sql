-- Кастомные триггеры Mini App (на чат)
CREATE TABLE IF NOT EXISTS chat_triggers (
    chat_id BIGINT NOT NULL,
    trigger_id TEXT NOT NULL,
    words JSONB NOT NULL,
    response TEXT NOT NULL,
    once_per_day BOOLEAN NOT NULL DEFAULT FALSE,
    match_mode TEXT NOT NULL DEFAULT 'exact',
    added_by_user_id BIGINT,
    added_by_username TEXT,
    sort_order INT NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, trigger_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_triggers_chat
ON chat_triggers (chat_id, sort_order);

-- Лимит «раз в день» для триггеров
CREATE TABLE IF NOT EXISTS trigger_daily (
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    trigger_id TEXT NOT NULL,
    used_on DATE NOT NULL DEFAULT CURRENT_DATE,
    PRIMARY KEY (chat_id, user_id, trigger_id, used_on)
);
