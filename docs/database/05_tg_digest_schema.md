# TG Digest DB Schema (актуально на 2026-02-27)

Документ описывает фактическую модель данных сервиса после применения:

- базовой схемы `tg_digest_system/tg_digest_system/db/schema.sql`
- миграций `001...010` из `tg_digest_system/tg_digest_system/db/migrations/`

## 1. Назначение схем

- `tg` — входные данные Telegram (сообщения, медиа, OCR, каналы/получатели).
- `rpt` — состояние обработки и результаты дайджестов/доставки.
- `vec` — RAG-слой (эмбеддинги и документы, опционально).
- `public` — runtime-конфигурация веба и multi-user сущности (`users`, `web_channels`, промпты, auth).

## 2. Ключевой принцип изоляции данных

Основная модель сервиса мультипользовательская: доступ к данным и настройкам изолируется через `users.id`.

`user_id` добавлен в рабочие таблицы миграцией `001_add_user_id.sql`:

- `tg.messages`
- `tg.media`
- `tg.media_text`
- `rpt.report_state`
- `rpt.digests`
- `rpt.deliveries`
- `vec.embeddings`
- `vec.documents`

Пользовательские конфигурации (`web_channels`, `channel_prompts`, credentials) также привязаны к `users.id`.

## 3. Базовые таблицы (schema.sql)

### 3.1 Сбор и обработка Telegram

- `tg.channels` — каталог отслеживаемых сущностей Telegram.
- `tg.recipients` — получатели дайджестов.
- `tg.channel_recipients` — связка каналов и получателей.
- `tg.messages` — поток сообщений.
- `tg.media` — медиафайлы/метаданные.
- `tg.media_text` — OCR-результаты по медиа.

### 3.2 Отчеты и состояние

- `rpt.report_state` — курсор `last_msg_id` по каналу/пользователю.
- `rpt.digests` — история сгенерированных дайджестов.
- `rpt.deliveries` — результаты доставки дайджестов.

### 3.3 RAG (опционально)

- `vec.embeddings` — чанки текста и эмбеддинги.
- `vec.documents` — метаданные сводных документов.

## 4. Таблицы веб-слоя и мультипользовательского контура

### 4.1 Пользователи и сессии

- `users` (`001`) — базовый профиль пользователя (`telegram_id`, `username`, активность).
- `user_sessions` (`002`) — web-сессии с `session_token` и `expires_at`.
- `user_identities` (`007`) — внешние OAuth-идентичности.
- `user_local_auth` (`010`) — локальная auth для тестового контура (`login`, `password_hash`, `is_active`).
- `audit_log` (`007`) — аудит действий в веб-контуре.

### 4.2 Каналы/промпты/доставка

- `web_channels` (`001`, `003`, `008`) — пользовательские каналы и настройки:
  - chat metadata,
  - `prompt_text`/`consolidated_doc_prompt_text`,
  - `delivery_*` параметры доставки.
- `channel_prompts` (`004`) — именованные промпты на канал.
- `prompt_library` (`005`, `009`) — библиотека промптов:
  - `owner_user_id`,
  - `visibility` (`public`/`private`),
  - `is_base` для системных шаблонов.

### 4.3 Telethon/Bot runtime

- `user_telegram_credentials` (`009`) — персональные Telethon-параметры пользователя:
  - `tg_api_id`, `tg_api_hash`, `tg_phone`, `tg_session_file`, `is_active`.
- `user_bot_credentials` (`009`) — боты пользователя для рассылки:
  - `bot_token`, `is_default`, `is_active`.
- `user_secret_files` (`009`) — учёт сгенерированных секрет-файлов пользователя.
- `bots` (`006`) — дополнительная таблица бот-учеток (legacy/совместимость).
- `entity_settings` (`006`) — универсальные key/value настройки сущностей.

## 5. Логические связи (сокращенно)

- `users (1) -> (N) web_channels`
- `users (1) -> (N) channel_prompts`
- `users (1) -> (1) user_telegram_credentials`
- `users (1) -> (N) user_bot_credentials`
- `users (1) -> (1) user_local_auth`
- `web_channels (1) -> (N) channel_prompts`
- `rpt.digests (1) -> (N) rpt.deliveries`
- рабочие таблицы `tg/rpt/vec` привязаны к `users.id` через `user_id`

## 6. Индексы и ограничения, критичные для работы

- Уникальность Telegram-сообщений: `uq_messages_peer_msg`.
- Уникальность канала в контуре пользователя: `web_channels UNIQUE(user_id, telegram_chat_id)`.
- Уникальность локального логина: `user_local_auth.login UNIQUE`.
- Один дефолтный бот на пользователя: partial index `idx_user_bot_credentials_default_one`.
- Уникальная запись Telethon credentials на пользователя: `user_telegram_credentials.user_id UNIQUE`.

## 7. Что важно учитывать при изменениях

- Любая новая business-таблица должна иметь `user_id` или явную связь с сущностью пользователя.
- Для multi-user API нельзя выполнять SELECT/UPDATE без фильтра по текущему пользователю.
- Миграции `009` и `010` обязательны для локальной авторизации и персонального Telethon runtime.

## 8. Источники истины

- База: `tg_digest_system/tg_digest_system/db/schema.sql`
- Миграции: `tg_digest_system/tg_digest_system/db/migrations/*.sql`
- Web/API логика: `tg_digest_system/tg_digest_system/web/web_api.py`
