# Конфигурация и хранение данных

## 1. Секреты и ключи API — отдельный файл

Все ключи, токены и пароли хранятся **вне репозитория** в файле **`secrets.env`**.

- **Шаблон:** `docker/secrets.env.example` — скопируйте в `secrets.env` и заполните.
- **Расположение:** `docker/secrets.env` или корень проекта `secrets.env`.
- **Загрузка:** при деплое (`deploy.sh`) и запуске веб-приложения автоматически подгружаются `.env` и `secrets.env`. Веб-приложение при старте ищет `secrets.env` в каталогах репо и подгружает его через `python-dotenv`.
- **В `.gitignore`:** `secrets.env` и `docker/secrets.env` — не коммитить.

Переменные в `secrets.env.example`:

- PostgreSQL: `PGPASSWORD`
- Telegram: `TG_API_ID`, `TG_API_HASH`, `TG_BOT_TOKEN`
- OpenAI: `OPENAI_API_KEY`
- Опционально: GitLab, OCR-провайдеры

Остальные настройки (порты, имена БД, пути) — в **`.env`** (можно коммитить `.env.example`).

---

## 2. Настройки чатов, ботов и пользователей — в БД

Все настройки системы (чаты, боты, пользователи) пишутся **в БД** и используются в том числе для работы с AI/RAG.

### Таблицы

- **`users`** — пользователи (telegram_id, name и т.д.).
- **`web_channels`** — каналы/чаты для мониторинга (telegram_chat_id, name, recipient, промпты и т.д.).
- **`channel_prompts`** — промпты по каналам (digest, consolidated).
- **`entity_settings`** (миграция 006) — ключ–значение по сущностям:
  - `entity_type`: `user` | `channel` | `bot` | `system`
  - `entity_id`: id пользователя, канала, бота или 0 для system
  - `key`, `value` (JSONB)
- **`bots`** (миграция 006) — боты пользователей; токен не хранится в БД, только **`token_ref`** — имя переменной в `secrets.env` (например `TG_BOT_TOKEN` или `BOT_1_TOKEN`).

### API настроек

- **GET `/api/settings?entity_type=channel&entity_id=1`** — список настроек сущности.
- **PUT `/api/settings`** — записать настройку (body: `entity_type`, `entity_id`, `key`, `value`).

Каналы и пользователи берутся из **`web_channels`** и **`users`**; доп. параметры — из **`entity_settings`**.

---

## 3. БД как единое хранилище для приложения и RAG (AI)

Одна и та же PostgreSQL-база используется:

1. **Для приложения:** пользователи, каналы, промпты, настройки (`users`, `web_channels`, `channel_prompts`, `entity_settings`, `bots`, `prompt_library` и т.д.).
2. **Для RAG и работы с AI:** схема **`vec`** — эмбеддинги и документы:
   - **`vec.embeddings`** — эмбеддинги текстов (сообщения, OCR, дайджесты, сводные доки).
   - **`vec.documents`** — сводные документы по чатам.

Подключение к БД задаётся в `.env` / `secrets.env`: `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`. И приложение, и воркеры, и будущие RAG/AI-сервисы используют эту же БД.

Миграции: `db/migrations/` (001–006). После деплоя выполните миграции (в т.ч. 006), чтобы появились `entity_settings` и `bots`.
