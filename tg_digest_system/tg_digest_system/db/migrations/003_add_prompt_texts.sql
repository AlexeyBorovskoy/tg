-- Миграция 003: Добавление полей для хранения текста промптов
-- Дата: 2026-02-06
-- Описание: Добавляем поля для хранения текста промптов прямо в БД для возможности редактирования через веб-интерфейс

-- Добавляем поля для хранения текста промптов
ALTER TABLE web_channels 
ADD COLUMN IF NOT EXISTS prompt_text TEXT,
ADD COLUMN IF NOT EXISTS consolidated_doc_prompt_text TEXT;

-- Комментарии к полям
COMMENT ON COLUMN web_channels.prompt_text IS 'Текст промпта для генерации дайджестов (если задан, используется вместо prompt_file)';
COMMENT ON COLUMN web_channels.consolidated_doc_prompt_text IS 'Текст промпта для генерации сводного документа (если задан, используется вместо consolidated_doc_prompt_file)';

-- Индекс для поиска каналов с кастомными промптами (опционально)
CREATE INDEX IF NOT EXISTS idx_web_channels_has_custom_prompts 
ON web_channels(user_id) 
WHERE prompt_text IS NOT NULL OR consolidated_doc_prompt_text IS NOT NULL;
