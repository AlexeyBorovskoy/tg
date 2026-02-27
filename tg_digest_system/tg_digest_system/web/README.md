# TG Digest Web Interface

Веб-интерфейс для управления каналами мониторинга Telegram.

## Установка

```bash
cd tg_digest_system/web
pip install -r requirements.txt
```

## Запуск

```bash
# Убедитесь что переменные окружения загружены
source ../.env

# Запуск через uvicorn
uvicorn web_api:app --host 0.0.0.0 --port 8080

# Или через Python
python web_api.py
```

## Структура

- `web_api.py` - FastAPI приложение с API endpoints
- `templates/` - HTML шаблоны (Jinja2)
  - `index.html` - Форма добавления канала
  - `channels.html` - Список каналов пользователя
- `static/` - Статические файлы (CSS, JS, изображения)

## API Endpoints

- `GET /` - Главная страница с формой добавления канала
- `GET /channels` - Страница со списком каналов
- `POST /api/users` - Создание/получение пользователя
- `GET /api/channels` - Список каналов пользователя
- `POST /api/channels` - Добавление нового канала
- `DELETE /api/channels/{channel_id}` - Удаление канала
- `GET /api/digests/{channel_id}` - Получение дайджестов канала
- `GET /health` - Healthcheck

## Переменные окружения

Используются те же переменные что и в основном воркере:
- `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` - PostgreSQL
- `TG_API_ID`, `TG_API_HASH`, `TG_SESSION_FILE` - Telegram API

Для тестового входа по `login/password`:
- `AUTH_LOCAL_ENABLED=1`
- `AUTH_LOCAL_SESSION_DAYS=30` (опционально)
- `AUTH_LOCAL_MIN_PASSWORD_LEN=8` (опционально)

Для входа через Яндекс OAuth:
- `AUTH_OWN_ENABLED=1`
- `YANDEX_OAUTH_CLIENT_ID=<...>`
- `YANDEX_OAUTH_CLIENT_SECRET=<...>`
- `BASE_URL=http(s)://<ваш_домен_или_ip>:<порт>`

После включения local auth доступны:
- `GET/POST /register` — регистрация `login/password` + привязка `telegram_id`
- `GET/POST /login` — вход по `login/password`
- `GET /setup` — персональная страница первичной настройки Telethon/бота

Поведение:
- после успешного входа/регистрации пользователь по умолчанию попадает на `/setup`;
- на `/setup` есть форма сохранения `tg_api_id/tg_api_hash/session_file/bot_token` в БД
  и пошаговая инструкция для интерактивной авторизации Telethon (ввод кода из Telegram).
- при одновременном `AUTH_LOCAL_ENABLED=1` и `AUTH_OWN_ENABLED=1` на `/login`
  доступны оба способа идентификации: `login/password` и кнопка `Войти через Яндекс`.

## Интеграция с воркером

Воркер (`digest_worker.py`) автоматически загружает каналы из БД через `config_db.py`:
- Каналы из БД имеют приоритет над каналами из `channels.json`
- Воркер обрабатывает каналы с учётом `user_id` (мультитенантность)
- Новые каналы из веб-интерфейса автоматически подхватываются воркером при следующем цикле
