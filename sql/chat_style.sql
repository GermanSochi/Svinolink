CREATE TABLE IF NOT EXISTS chat_style (
    chat_id BIGINT PRIMARY KEY,
    style_notes TEXT NOT NULL DEFAULT '',
    sample_phrases JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
