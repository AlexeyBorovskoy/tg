# Деплой TG Digest System (Docker)

**Секреты и ключи API** хранятся в отдельном файле **`secrets.env`** (см. `secrets.env.example`). Не коммитить. Подробнее: [CONFIG.md](../CONFIG.md).

## Два варианта деплоя

- **С нуля** — поднимается контейнер PostgreSQL, миграции, web и worker (данные в Docker volumes).
- **С существующими данными** — используется уже работающая БД, промпты и конфиг из репо, сессия Telethon и данные воркера с хоста.

---

## Вариант 1: Деплой с нуля

1. **Клонируйте репозиторий** и перейдите в каталог с docker-конфигурацией:
   ```bash
   cd /path/to/tg_digest_system/tg_digest_system/docker
   ```

2. **Создайте `.env`** из примера и заполните переменные:
   ```bash
   cp .env.example .env
   # Отредактируйте .env: PGPASSWORD, TG_API_ID, TG_API_HASH, OPENAI_API_KEY
   ```

3. **Запустите деплой**:
   ```bash
   chmod +x deploy.sh
   ./deploy.sh
   ```
   Или с пересборкой образов:
   ```bash
   ./deploy.sh --build
   ```

4. **Веб-интерфейс**: http://localhost:8000 (или порт из `WEB_PORT` в `.env`).

5. **Сессия Telegram** (для проверки чатов и воркера): при первом запуске создайте сессию:
   ```bash
   docker compose run --rm auth
   ```
   Введите код из Telegram. После этого общий volume `worker_data` будет содержать `telethon.session`, и веб-интерфейс сможет проверять доступ к чатам.

---

## Вариант 2: Деплой с существующими данными (БД, промпты, настройки)

Используется **уже существующая** PostgreSQL, **промпты и конфиг из репо**, а также **каталог данных воркера** (сессия Telethon, логи, media) на хосте.

1. **Перейдите в каталог docker**:
   ```bash
   cd /path/to/tg_digest_system/tg_digest_system/docker
   ```

2. **Создайте/заполните `.env`**:
   - **PGHOST** — хост существующей БД (обязательно). На Mac/Windows: `host.docker.internal`; на Linux можно тоже задать `host.docker.internal` (в `docker-compose.existing.yml` добавлен `host-gateway` для доступа к хосту).
   - **PGPORT**, **PGDATABASE**, **PGUSER**, **PGPASSWORD** — параметры подключения к этой БД.
   - Остальное как обычно: **TG_API_ID**, **TG_API_HASH**, **OPENAI_API_KEY** и т.д.

3. **Подготовьте каталоги данных** (опционально, скрипт создаст при необходимости):
   ```bash
   chmod +x prepare-existing-data.sh deploy.sh
   ./prepare-existing-data.sh
   ```
   По умолчанию создаётся `./data/worker_data`, `./data/logs`, `./data/media`. Сессию `telethon.session` положите в `./data/worker_data/` или создайте её после запуска через `docker compose ... run --rm auth`.

4. **Запустите деплой с существующими данными**:
   ```bash
   ./deploy.sh --existing
   ```
   Или вручную:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.existing.yml up -d
   ```
   Контейнер PostgreSQL при этом **не запускается** — используется БД с хоста. Промпты и конфиг монтируются из репо: `../prompts`, `../config`. Данные воркера — из `./data/worker_data`, `./data/logs`, `./data/media`.

5. **Переменные для путей** (при необходимости задать в `.env`):
   - **EXISTING_DATA_DIR** — каталог с подкаталогами `worker_data`, `logs`, `media` (по умолчанию `./data`).
   - **EXISTING_PROMPTS_DIR** — каталог промптов (по умолчанию `../prompts`).
   - **EXISTING_CONFIG_DIR** — каталог конфига (по умолчанию `../config`).

---

## Сервисы

| Сервис    | Назначение |
|-----------|------------|
| **postgres** | PostgreSQL 16: схема из `db/schema.sql` при первом запуске |
| **migrate**  | Однократный запуск миграций 001–005 после postgres |
| **web**      | FastAPI: добавление каналов, промпты, проверка чатов/получателей |
| **worker**   | Воркер дайджестов (Telethon + OpenAI) |
| **auth**     | Профиль `tools`: первичная авторизация Telethon |

---

## Полезные команды

```bash
# Логи
docker compose logs -f web
docker compose logs -f worker

# Только миграции (без поднятия всех сервисов)
./deploy.sh --migrate

# Остановка
docker compose down

# Остановка с удалением volumes (БД и данные воркера будут удалены)
docker compose down -v
```

---

## Сетевые настройки и проверка порта 5433

PostgreSQL в контейнере по умолчанию публикуется на **порт 5433** на хосте (привязка **127.0.0.1**), чтобы не конфликтовать с системным PostgreSQL на 5432.

**Проверка порта 5433 после деплоя:**
```bash
./check-port-5433.sh
```
Или вручную:
```bash
nc -zv 127.0.0.1 5433
psql -h 127.0.0.1 -p 5433 -U tg_digest -d tg_digest -c 'SELECT 1'
```

В `.env` можно задать:
- **POSTGRES_HOST_PORT** — порт на хосте (по умолчанию 5433).
- **POSTGRES_BIND_ADDRESS** — интерфейс (по умолчанию 127.0.0.1; для доступа с других машин — 0.0.0.0, только при защищённой сети).

---

## Переменные окружения (.env)

Обязательные для работы веб и воркера:

- **PGPASSWORD** — пароль PostgreSQL
- **TG_API_ID**, **TG_API_HASH** — из https://my.telegram.org
- **TG_BOT_TOKEN** — токен бота (если используется бот)
- **OPENAI_API_KEY** — ключ OpenAI

Для проверки чатов в веб-интерфейсе нужна сессия Telethon (шаг 5 выше). Переменные **TG_SESSION_FILE** в контейнерах указывают на общий volume `worker_data`.

---

## Структура

- `Dockerfile` — образ воркера (digest_worker.py)
- `Dockerfile.web` — образ веб-интерфейса (FastAPI/uvicorn)
- `docker-compose.yml` — postgres, migrate, web, worker, auth
- `deploy.sh` — скрипт деплоя (create .env, up -d)
- `.env.example` — шаблон переменных окружения

Миграции лежат в `../db/migrations/` (001–005). Схема БД при первом запуске postgres берётся из `../db/schema.sql`.
