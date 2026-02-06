-- Миграция 001: Добавление поддержки мультитенантности (user_id)
-- Дата: 2026-02-06
-- Описание: Добавляет user_id во все таблицы для изоляции данных пользователей

-- 1. Создаём таблицу пользователей
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    name TEXT,
    email TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active BOOLEAN NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active) WHERE is_active = true;

COMMENT ON TABLE users IS 'Пользователи веб-интерфейса (мультитенантность)';

-- 2. Добавляем user_id в tg.messages
ALTER TABLE tg.messages 
ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_tg_messages_user_peer ON tg.messages(user_id, peer_type, peer_id, msg_id);
CREATE INDEX IF NOT EXISTS idx_tg_messages_user_dt ON tg.messages(user_id, dt);

-- Миграция существующих данных: присваиваем user_id=1 (основной пользователь)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM users WHERE id = 1) THEN
        -- Пользователь уже существует
        NULL;
    ELSE
        -- Создаём основного пользователя
        INSERT INTO users (id, telegram_id, name, is_active)
        VALUES (1, 499412926, 'Alexey Borovskoy (Main)', true)
        ON CONFLICT DO NOTHING;
    END IF;
    
    -- Присваиваем всем существующим сообщениям user_id=1
    UPDATE tg.messages SET user_id = 1 WHERE user_id IS NULL;
END $$;

-- 3. Добавляем user_id в tg.media
ALTER TABLE tg.media 
ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_tg_media_user_peer ON tg.media(user_id, peer_type, peer_id, msg_id);

UPDATE tg.media SET user_id = 1 WHERE user_id IS NULL;

-- 4. Добавляем user_id в tg.media_text
ALTER TABLE tg.media_text 
ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_tg_media_text_user_peer ON tg.media_text(user_id, peer_type, peer_id, msg_id);

UPDATE tg.media_text SET user_id = 1 WHERE user_id IS NULL;

-- 5. Добавляем user_id в rpt.report_state
ALTER TABLE rpt.report_state 
ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;

CREATE UNIQUE INDEX IF NOT EXISTS idx_rpt_report_state_user_peer 
ON rpt.report_state(user_id, peer_type, peer_id);

UPDATE rpt.report_state SET user_id = 1 WHERE user_id IS NULL;

-- 6. Добавляем user_id в rpt.digests
ALTER TABLE rpt.digests 
ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_rpt_digests_user_peer ON rpt.digests(user_id, peer_type, peer_id);
CREATE INDEX IF NOT EXISTS idx_rpt_digests_user_created ON rpt.digests(user_id, created_at DESC);

UPDATE rpt.digests SET user_id = 1 WHERE user_id IS NULL;

-- 7. Добавляем user_id в rpt.deliveries
ALTER TABLE rpt.deliveries 
ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_rpt_deliveries_user ON rpt.deliveries(user_id, digest_id);

UPDATE rpt.deliveries SET user_id = 1 WHERE user_id IS NULL;

-- 8. Добавляем user_id в vec.embeddings (RAG)
ALTER TABLE vec.embeddings 
ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_vec_embeddings_user_peer ON vec.embeddings(user_id, peer_type, peer_id);

UPDATE vec.embeddings SET user_id = 1 WHERE user_id IS NULL;

-- 9. Добавляем user_id в vec.documents
ALTER TABLE vec.documents 
ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;

CREATE UNIQUE INDEX IF NOT EXISTS idx_vec_documents_user_peer 
ON vec.documents(user_id, peer_type, peer_id);

UPDATE vec.documents SET user_id = 1 WHERE user_id IS NULL;

-- 10. Создаём таблицу каналов (для веб-интерфейса)
CREATE TABLE IF NOT EXISTS web_channels (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    telegram_chat_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    peer_type TEXT NOT NULL DEFAULT 'channel',
    prompt_file TEXT NOT NULL DEFAULT 'prompts/digest_management.md',
    consolidated_doc_path TEXT,
    consolidated_doc_prompt_file TEXT DEFAULT 'prompts/consolidated_engineering.md',
    poll_interval_minutes INTEGER NOT NULL DEFAULT 60,
    enabled BOOLEAN NOT NULL DEFAULT true,
    recipient_telegram_id BIGINT NOT NULL,
    recipient_name TEXT,
    -- Метод доступа к чату
    access_method TEXT DEFAULT 'system_session' CHECK (access_method IN ('bot', 'user_session', 'system_session')),
    -- Статус доступа
    access_status TEXT DEFAULT 'pending' CHECK (access_status IN ('pending', 'available', 'unavailable', 'requires_setup')),
    -- Требуется ли добавление бота
    bot_required BOOLEAN DEFAULT false,
    -- Инструкции для клиента (если требуется настройка)
    setup_instructions TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, telegram_chat_id)
);

CREATE INDEX IF NOT EXISTS idx_web_channels_user ON web_channels(user_id);
CREATE INDEX IF NOT EXISTS idx_web_channels_enabled ON web_channels(enabled) WHERE enabled = true;

COMMENT ON TABLE web_channels IS 'Каналы добавленные через веб-интерфейс (мультитенантность)';

-- 11. Функция обновления updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trg_web_channels_updated_at BEFORE UPDATE ON web_channels
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
