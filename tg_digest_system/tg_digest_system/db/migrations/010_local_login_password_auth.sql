-- Миграция 010: Локальная авторизация логин/пароль
-- Дата: 2026-02-20
-- Описание:
--   Тестовый контур: классическая регистрация и вход по логину/паролю.
--   Все данные пользователя далее изолируются по users.id (user_id).

CREATE TABLE IF NOT EXISTS user_local_auth (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    login TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_local_auth_login ON user_local_auth(login);
CREATE INDEX IF NOT EXISTS idx_user_local_auth_active ON user_local_auth(is_active) WHERE is_active = true;

COMMENT ON TABLE user_local_auth IS 'Локальные учетные записи (login/password_hash) для тестового контура';
COMMENT ON COLUMN user_local_auth.password_hash IS 'PBKDF2 хэш в формате: pbkdf2_sha256$iterations$salt$hash';

DROP TRIGGER IF EXISTS trg_user_local_auth_updated_at ON user_local_auth;
CREATE TRIGGER trg_user_local_auth_updated_at
BEFORE UPDATE ON user_local_auth
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

