# DEPLOY_NILA.md

## Цель
Развернуть `transcription_system` на сервере Нила с:
- единым входом и общей сессией с TG Digest;
- ролью `Alex` как admin;
- удалением исходного аудио после обработки.

## 1. Переменные окружения

```env
AUTH_LOCAL_ENABLED=1
AUTH_SHARED_ENABLED=1
AUTH_SHARED_COOKIE_NAME=session_token
AUTH_SHARED_ADMIN_LOGIN=Alex
AUTH_SHARED_LOGIN_URL=http://89.124.65.229:8010/login
AUTH_SHARED_REGISTER_URL=http://89.124.65.229:8010/register

PGHOST=<tg-digest-postgres-host>
PGPORT=5432
PGDATABASE=tg_digest
PGUSER=tg_digest
PGPASSWORD=<secret>

ASSEMBLYAI_API_KEY=<secret>
OPENAI_API_KEY=<secret>
OPENAI_BASE_URL=<endpoint>
OPENAI_MODEL=<model>

KEEP_UPLOADED_AUDIO=0
RESOURCE_TG_DIGEST_URL=http://89.124.65.229:8010/setup
```

## 2. Установка зависимостей

```bash
cd /opt/transcription_system
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Важно: должен установиться `psycopg2-binary` (нужен для shared auth).

## 3. Старт

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8081
```

## 4. Smoke-check

1. Открыть `/login` и выполнить вход под `Alex`.
2. Проверить доступность `/users` (admin-only).
3. Проверить, что пользователь non-admin не видит админ-раздел.
4. Запустить job на небольшом audio.
5. Убедиться, что исходный файл удален после статуса `done/error`.
6. Проверить переход в TG Digest и обратно в пределах одной сессии.

## 5. Откат

- `AUTH_SHARED_ENABLED=0` и перезапуск сервиса: возврат к локальной SQLite-авторизации.
- `KEEP_UPLOADED_AUDIO=1` (временно) если нужно сохранить исходники для отладки.

## 6. Документирование на сервере

После успешного деплоя внести запись в `/opt/server-docs/logbook.md`.
Шаблон записи: `_release_docs/NILA_LOGBOOK_ENTRY_TRANSCRIPTION.md`.
