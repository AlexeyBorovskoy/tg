-- Миграция 007: OAuth-идентификации (Google/Яндекс), аудит действий
-- Дата: 2026-02-06
-- Описание: Наш сервис сам делает OAuth и выдаёт свои токены.
--           user_identities — привязка пользователей к Google/Яндекс;
--           audit_log — кто и что делал в системе.

-- -----------------------------------------------------------------------------
-- users: разрешаем telegram_id NULL для пользователей только через OAuth
-- -----------------------------------------------------------------------------
ALTER TABLE users ALTER COLUMN telegram_id DROP NOT NULL;
-- В PostgreSQL UNIQUE уже допускает несколько NULL; индекс для поиска по telegram_id оставляем

-- -----------------------------------------------------------------------------
-- user_identities — привязка к внешним провайдерам (Google, Yandex)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_identities (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('google', 'yandex')),
    external_id TEXT NOT NULL,
    email TEXT,
    display_name TEXT,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, external_id)
);

CREATE INDEX IF NOT EXISTS idx_user_identities_user ON user_identities(user_id);
CREATE INDEX IF NOT EXISTS idx_user_identities_provider ON user_identities(provider, external_id);

COMMENT ON TABLE user_identities IS 'Привязка пользователей к OAuth-провайдерам (Google, Yandex)';

-- -----------------------------------------------------------------------------
-- audit_log — журнал действий пользователей
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    details JSONB DEFAULT '{}',
    ip INET,
    user_agent TEXT,
    resource_type TEXT,
    resource_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_at ON audit_log(at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_log_resource ON audit_log(resource_type, resource_id);

COMMENT ON TABLE audit_log IS 'Аудит: кто и что делал в системе (вход, выход, создание/изменение каналов и т.д.)';
