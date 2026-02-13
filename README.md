# TG Digest System

TG Digest System автоматизирует мониторинг Telegram-чатов/каналов и превращает поток сообщений в:

- управленческие дайджесты (по новым сообщениям и OCR),
- сводные инженерные документы (docs-as-code) по каждому чату,
- журнал доставок и историю генераций в PostgreSQL,
- (опционально) RAG-индексацию в pgvector.

Пайплайн: **Telegram → PostgreSQL → OCR → LLM → доставка в Telegram**.

---

## Содержание

1. Возможности
2. Архитектура
3. Структура репозитория
4. Быстрый старт (Docker)
5. Конфигурация и секреты
6. Веб-интерфейс
7. Эксплуатация и диагностика
8. База данных (кратко)
9. Безопасность

---

## 1. Возможности

- Сбор новых сообщений (Telethon) и запись в PostgreSQL.
- Скачивание медиа и OCR:
  - локальный Tesseract,
  - или облачные провайдеры (через unified OCR сервис) с fallback.
- Генерация дайджеста LLM (OpenAI API) с трассировкой `msg_id=...`.
- Доставка в Telegram через Bot API:
  - текстом и/или файлом,
  - важность (important/informational), лимит текста и режим summary.
- Сводный инженерный документ по чату:
  - хранится как markdown файл в репозитории (docs-as-code),
  - обновляется на основании последних сообщений/OCR,
  - (опционально) индексируется в pgvector.
- Web UI (FastAPI):
  - добавление каналов,
  - управление промптами и настройками в БД,
  - просмотр последних дайджестов и документов.

---

## 2. Архитектура

```text
┌─────────────┐   Telethon   ┌───────────────┐    OCR     ┌───────────────┐
│ Telegram    │ ───────────▶ │ PostgreSQL     │ ────────▶ │ tg.media_text  │
│ chats       │              │ tg.messages    │           │ (OCR results)  │
└─────────────┘              │ tg.media       │           └───────────────┘
                              │ rpt.digests    │
                              │ rpt.deliveries │
                              └───────┬───────┘
                                      │
                                      ▼
                                ┌───────────────┐
                                │ LLM (OpenAI)   │
                                │ digest/doc gen │
                                └───────┬───────┘
                                        │
                                        ▼
                                 ┌──────────────┐
                                 │ Telegram Bot  │
                                 │ delivery      │
                                 └──────────────┘

Опционально:
┌───────────────┐
│ vec (pgvector) │  embeddings: digest / consolidated_doc / ...
└───────────────┘
```

---

## 3. Структура репозитория

Основная система находится в `tg_digest_system/tg_digest_system/`:

```text
tg_digest_system/tg_digest_system/
  scripts/                 Python-код воркера и утилит
    digest_worker.py       основной воркер (цикл, step-режим)
    telegram_client.py     Telethon чтение + Bot API отправка
    database.py            PostgreSQL слой
    llm.py                 OpenAI клиент (с прокси)
    ocr.py                 OCR Tesseract
    ocr_service_unified.py OCR с облачными провайдерами (fallback)
    rag.py                 pgvector embeddings (опционально)
    gitlab_push.py         git add/commit/push документов и дайджестов
  web/                     FastAPI UI и API управления
  db/                      schema.sql + migrations
  docker/                  docker-compose, env/secrets, tunnel-скрипты
  config/                  channels.json / channels.v2.json / delivery settings
  prompts/                 промпты LLM
  docs/                    документация по развёртыванию
```

В корне репозитория также есть `deploy/`, `scripts/` и `docs/` (часть из них legacy/вспомогательные).

---

## 4. Быстрый старт (Docker)

### Требования

- Docker 24+ и Docker Compose 2+
- Доступ к Telegram API и OpenAI API (при необходимости через proxy/tunnel)

### Запуск

```bash
cd tg_digest_system/tg_digest_system/docker

cp .env.example .env
cp secrets.env.example secrets.env

# заполните secrets.env: TG_API_ID, TG_API_HASH, TG_BOT_TOKEN, OPENAI_API_KEY, JWT_SECRET и др.
# заполните .env: PGPASSWORD, WEB_PORT и др.

# Telethon авторизация (один раз, создаст /app/data/telethon.session во volume)
docker compose run --rm auth

docker compose up -d
docker compose logs -f worker
```

---

## 5. Конфигурация и секреты

### Где что лежит

- Секреты: `tg_digest_system/tg_digest_system/docker/secrets.env` (не коммитить)
- Несеcretные настройки: `tg_digest_system/tg_digest_system/docker/.env`
- Каналы:
  - файл: `tg_digest_system/tg_digest_system/config/channels.json`
  - и/или БД: `web_channels` (через Web UI)
- Промпты:
  - приоритет: БД (`channel_prompts`, `web_channels.prompt_text`)
  - fallback: файлы `tg_digest_system/tg_digest_system/prompts/*.md`

### Критичные переменные

- Telegram (Telethon): `TG_API_ID`, `TG_API_HASH`, `TG_SESSION_FILE`
- Telegram (Bot API доставка): `TG_BOT_TOKEN`
- OpenAI: `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL` (опционально)
- PostgreSQL: `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`
- Proxy/tunnel: `HTTPS_PROXY`, `HTTP_PROXY` (например `socks5://<host>:1080`)

---

## 6. Веб-интерфейс

Код: `tg_digest_system/tg_digest_system/web/web_api.py`.

Назначение:

- добавление и управление чатами (в `web_channels`),
- управление промптами (в `channel_prompts`),
- настройки доставки (в `web_channels.delivery_*`),
- просмотр последних дайджестов/документов.

Авторизация:

- встроенная (OAuth Яндекс + JWT) или внешний auth-сервис (см. `tg_digest_system/tg_digest_system/docker/secrets.env.example`).

---

## 7. Эксплуатация и диагностика

### Полезные команды (Docker)

```bash
cd tg_digest_system/tg_digest_system/docker

docker compose ps
docker compose logs -f worker
docker compose logs -f web
```

### Step-режим воркера

```bash
docker exec -it tg_digest_worker python /app/scripts/digest_worker.py --once --step text
docker exec -it tg_digest_worker python /app/scripts/digest_worker.py --once --step media
docker exec -it tg_digest_worker python /app/scripts/digest_worker.py --once --step ocr
docker exec -it tg_digest_worker python /app/scripts/digest_worker.py --once --step digest
```

### Heartbeat

Воркер пишет файл `/app/logs/heartbeat.txt`. Если он “старый”, воркер не проходит цикл или упал.

### Типовые проблемы

1) Дайджест генерируется, но не приходит в Telegram:
   - проверьте `TG_BOT_TOKEN` (ошибка Bot API `404 Not Found` = токен пустой/неверный),
   - проверьте, что пользователь/чат начал диалог с ботом и бот имеет право писать.
2) Ошибки OpenAI `APIConnectionError`, `ConnectError`, timeouts:
   - проверьте `HTTPS_PROXY`/`HTTP_PROXY` и доступность SOCKS/HTTP proxy,
   - проверьте, что на хосте реально слушается порт туннеля (например `1080`).
3) “Новых сообщений нет”, хотя они есть:
   - проверьте доступ Telethon аккаунта к чату,
   - проверьте курсор `rpt.report_state.last_msg_id` и реальный `max(msg_id)` в `tg.messages`.

---

## 8. База данных (кратко)

Ключевые таблицы:

- `tg.messages` — сообщения
- `tg.media` — медиа (часто хранится на диске, `local_path`)
- `tg.media_text` — OCR результаты
- `rpt.report_state` — курсор обработки
- `rpt.digests` — история дайджестов
- `rpt.deliveries` — журнал доставок
- `web_channels`, `users`, `channel_prompts` — web-конфигурация
- `vec.*` — RAG (pgvector), если включено

Схема и миграции:
- `tg_digest_system/tg_digest_system/db/schema.sql`
- `tg_digest_system/tg_digest_system/db/migrations/*.sql`

---

## 9. Безопасность

- Не коммитьте секреты: используйте `docker/secrets.env`.
- Для web UI используйте авторизацию (OAuth/JWT или внешний auth).
- Для git push из прода используйте deploy key с минимальными правами.

