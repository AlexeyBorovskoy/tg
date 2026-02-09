-- Миграция 006: Настройки чатов, ботов и пользователей в БД (для RAG и AI)
-- Дата: 2026-02-06
-- Описание: Все настройки системы (чаты, боты, пользователи) хранятся в БД.
--           БД используется и для приложения, и для RAG (vec.embeddings, vec.documents).

-- -----------------------------------------------------------------------------
-- entity_settings — настройки по сущностям (user, channel, bot)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entity_settings (
    id SERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('user', 'channel', 'bot', 'system')),
    entity_id INTEGER NOT NULL DEFAULT 0,
    key TEXT NOT NULL,
    value JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (entity_type, entity_id, key)
);

CREATE INDEX IF NOT EXISTS idx_entity_settings_entity ON entity_settings(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_settings_key ON entity_settings(key);

COMMENT ON TABLE entity_settings IS 'Настройки пользователей, каналов и ботов (для работы с AI/RAG)';
COMMENT ON COLUMN entity_settings.entity_type IS 'user=users.id, channel=web_channels.id, bot=bots.id, system=глобальные';
COMMENT ON COLUMN entity_settings.entity_id IS '0 для system, иначе id из соответствующей таблицы';

DROP TRIGGER IF EXISTS trg_entity_settings_updated_at ON entity_settings;
CREATE TRIGGER trg_entity_settings_updated_at BEFORE UPDATE ON entity_settings
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- bots — боты системы (токен не храним в БД, только ссылка на секрет)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bots (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    token_ref TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bots_user ON bots(user_id);

COMMENT ON TABLE bots IS 'Боты: token_ref — имя переменной в secrets.env (например TG_BOT_TOKEN или BOT_1_TOKEN)';
COMMENT ON COLUMN bots.token_ref IS 'Ключ в secrets.env, откуда брать токен бота';

DROP TRIGGER IF EXISTS trg_bots_updated_at ON bots;
CREATE TRIGGER trg_bots_updated_at BEFORE UPDATE ON bots
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- Связь БД с RAG: все данные приложения в одной БД (public + vec + tg + rpt)
-- vec.embeddings, vec.documents уже созданы в schema.sql — используем для AI.
-- -----------------------------------------------------------------------------
