-- Миграция 009: Персональные Telegram runtime-настройки пользователей + приватность prompt_library
-- Дата: 2026-02-20
-- Описание:
--   1) Хранение Telegram API/сессии пользователя в БД (user_telegram_credentials)
--   2) Хранение пользовательских ботов для рассылки (user_bot_credentials)
--   3) Учёт сгенерированных файлов секретов пользователя (user_secret_files)
--   4) Публичные/приватные шаблоны в prompt_library

-- -----------------------------------------------------------------------------
-- prompt_library: видимость и владелец шаблона
-- -----------------------------------------------------------------------------
ALTER TABLE prompt_library
  ADD COLUMN IF NOT EXISTS owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public', 'private')),
  ADD COLUMN IF NOT EXISTS is_base BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_prompt_library_owner ON prompt_library(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_prompt_library_visibility ON prompt_library(visibility);
CREATE INDEX IF NOT EXISTS idx_prompt_library_owner_visibility ON prompt_library(owner_user_id, visibility);

-- Системные/базовые шаблоны (из файлов) доступны всем
UPDATE prompt_library
SET is_base = true, visibility = 'public'
WHERE owner_user_id IS NULL
  AND (file_path IS NOT NULL OR is_base = false);

COMMENT ON COLUMN prompt_library.owner_user_id IS 'Владелец пользовательского шаблона; NULL для системных (базовых)';
COMMENT ON COLUMN prompt_library.visibility IS 'public=доступен всем, private=только владельцу';
COMMENT ON COLUMN prompt_library.is_base IS 'Базовый системный шаблон (синхронизирован из prompts/)';

-- -----------------------------------------------------------------------------
-- Персональные Telegram credentials пользователя
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_telegram_credentials (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    tg_api_id INTEGER NOT NULL,
    tg_api_hash TEXT NOT NULL,
    tg_phone TEXT,
    tg_session_file TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_telegram_credentials_active
ON user_telegram_credentials(user_id, is_active);

COMMENT ON TABLE user_telegram_credentials IS 'Персональные Telethon credentials пользователя (api_id/api_hash/session_file)';
COMMENT ON COLUMN user_telegram_credentials.tg_session_file IS 'Путь к .session файлу пользователя';

DROP TRIGGER IF EXISTS trg_user_telegram_credentials_updated_at ON user_telegram_credentials;
CREATE TRIGGER trg_user_telegram_credentials_updated_at
BEFORE UPDATE ON user_telegram_credentials
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- Персональные боты пользователя для рассылки
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_bot_credentials (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bot_name TEXT NOT NULL DEFAULT 'Default Bot',
    bot_token TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    is_default BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_bot_credentials_user ON user_bot_credentials(user_id);
CREATE INDEX IF NOT EXISTS idx_user_bot_credentials_active ON user_bot_credentials(user_id, is_active);
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_bot_credentials_default_one
ON user_bot_credentials(user_id) WHERE is_default = true;

COMMENT ON TABLE user_bot_credentials IS 'Боты пользователей для отправки дайджестов (персональный токен на пользователя)';
COMMENT ON COLUMN user_bot_credentials.is_default IS 'Бот по умолчанию для пользователя';

DROP TRIGGER IF EXISTS trg_user_bot_credentials_updated_at ON user_bot_credentials;
CREATE TRIGGER trg_user_bot_credentials_updated_at
BEFORE UPDATE ON user_bot_credentials
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- Сгенерированные файлы секретов пользователя
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_secret_files (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    secret_file_path TEXT NOT NULL,
    file_checksum TEXT,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_secret_files_user ON user_secret_files(user_id);

COMMENT ON TABLE user_secret_files IS 'Метаданные сгенерированного env-файла с секретами пользователя';
COMMENT ON COLUMN user_secret_files.file_checksum IS 'SHA256 содержимого файла на момент генерации';

DROP TRIGGER IF EXISTS trg_user_secret_files_updated_at ON user_secret_files;
CREATE TRIGGER trg_user_secret_files_updated_at
BEFORE UPDATE ON user_secret_files
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

