-- Миграция 005: Библиотека шаблонов промптов в БД
-- Дата: 2026-02-06
-- Описание: Таблица для хранения шаблонов промптов (библиотека), синхронизируемых из файлов или создаваемых вручную

CREATE TABLE IF NOT EXISTS prompt_library (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    prompt_type TEXT NOT NULL CHECK (prompt_type IN ('digest', 'consolidated')),
    file_path TEXT,
    body TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(file_path)
);

CREATE INDEX IF NOT EXISTS idx_prompt_library_type ON prompt_library(prompt_type);

COMMENT ON TABLE prompt_library IS 'Библиотека шаблонов промптов (для выбора при создании канала и синхронизации из файлов)';
COMMENT ON COLUMN prompt_library.file_path IS 'Относительный путь к файлу (например prompts/digest_management.md)';
COMMENT ON COLUMN prompt_library.body IS 'Содержимое промпта';

DROP TRIGGER IF EXISTS trg_prompt_library_updated_at ON prompt_library;
CREATE TRIGGER trg_prompt_library_updated_at BEFORE UPDATE ON prompt_library
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
