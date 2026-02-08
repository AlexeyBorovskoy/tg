-- Миграция 004: Создание таблицы для хранения промптов
-- Дата: 2026-02-06
-- Описание: Создаём таблицу для хранения нескольких промптов на канал, аналогично проекту транскрибации

-- Таблица промптов
CREATE TABLE IF NOT EXISTS channel_prompts (
    id SERIAL PRIMARY KEY,
    channel_id INTEGER NOT NULL REFERENCES web_channels(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    prompt_type TEXT NOT NULL CHECK (prompt_type IN ('digest', 'consolidated')),
    name TEXT NOT NULL,
    text TEXT NOT NULL,
    is_default BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(channel_id, prompt_type, name)
);

CREATE INDEX IF NOT EXISTS idx_channel_prompts_channel ON channel_prompts(channel_id);
CREATE INDEX IF NOT EXISTS idx_channel_prompts_user ON channel_prompts(user_id);
CREATE INDEX IF NOT EXISTS idx_channel_prompts_type ON channel_prompts(channel_id, prompt_type);

COMMENT ON TABLE channel_prompts IS 'Промпты для каналов (можно создавать несколько промптов одного типа)';
COMMENT ON COLUMN channel_prompts.prompt_type IS 'Тип промпта: digest (для дайджестов) или consolidated (для сводного документа)';
COMMENT ON COLUMN channel_prompts.is_default IS 'Является ли промпт используемым по умолчанию для этого типа';

-- Триггер для обновления updated_at
CREATE TRIGGER trg_channel_prompts_updated_at BEFORE UPDATE ON channel_prompts
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Миграция существующих промптов из web_channels в channel_prompts
-- Создаём промпты по умолчанию из существующих данных
INSERT INTO channel_prompts (channel_id, user_id, prompt_type, name, text, is_default)
SELECT 
    wc.id,
    wc.user_id,
    'digest' as prompt_type,
    'Промпт для дайджестов' as name,
    COALESCE(wc.prompt_text, '') as text,
    true as is_default
FROM web_channels wc
WHERE NOT EXISTS (
    SELECT 1 FROM channel_prompts cp 
    WHERE cp.channel_id = wc.id AND cp.prompt_type = 'digest' AND cp.is_default = true
)
ON CONFLICT DO NOTHING;

INSERT INTO channel_prompts (channel_id, user_id, prompt_type, name, text, is_default)
SELECT 
    wc.id,
    wc.user_id,
    'consolidated' as prompt_type,
    'Промпт для сводного документа' as name,
    COALESCE(wc.consolidated_doc_prompt_text, '') as text,
    true as is_default
FROM web_channels wc
WHERE NOT EXISTS (
    SELECT 1 FROM channel_prompts cp 
    WHERE cp.channel_id = wc.id AND cp.prompt_type = 'consolidated' AND cp.is_default = true
)
ON CONFLICT DO NOTHING;
