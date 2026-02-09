# Авторизация в TG Digest System

## Вариант 1: Своя авторизация (OAuth Яндекс + свои JWT)

**Наш сервис сам делает OAuth и выдаёт свои токены.** Рекомендуемый вариант.

### Включение

- **AUTH_OWN_ENABLED=1** — включить свою авторизацию.
- **BASE_URL** — публичный URL приложения (для redirect_uri OAuth), например `https://digest.example.com`.
- **JWT_SECRET** — секрет для подписи JWT (длинная случайная строка). Без неё при каждом перезапуске токены станут недействительны.
- **YANDEX_OAUTH_CLIENT_ID**, **YANDEX_OAUTH_CLIENT_SECRET** — из [OAuth Яндекс](https://oauth.yandex.com/).

В кабинете OAuth Яндекс нужно добавить redirect_uri: `{BASE_URL}/auth/yandex/callback`.

### Поток

1. Пользователь нажимает «Войти через Яндекс» на странице `/login`.
2. Редирект на Яндекс → вход → callback на наш `/auth/yandex/callback`.
3. Backend обменивает `code` на данные пользователя (external_id, email, имя), находит или создаёт запись в **users** и **user_identities**, выдаёт **наш JWT** (access_token), ставит cookie `auth_token`, пишет в **audit_log** событие `login`.
4. Дальше все запросы проверяются по нашему JWT (заголовок `Authorization` или cookie). Выход — `/logout`, в audit_log пишется `logout`.

### БД (миграция 007)

- **user_identities** — привязка пользователя к провайдеру (provider, external_id, email, display_name).
- **audit_log** — кто и что делал: user_id, action, at, details (JSON), ip, user_agent, resource_type, resource_id.

События аудита: `login`, `logout`, `channel_created`, `channel_updated`, `channel_deleted`, `prompt_created`, `prompt_updated`, `prompt_deleted`, `settings_updated`.

### Код

- **`web/auth_own.py`** — JWT (create_access_token, verify_access_token), OAuth URL и обмен code (Яндекс).
- **`web_api.py`** — маршруты `/auth/yandex`, `/auth/yandex/callback`; зависимость `get_current_auth_user` при `AUTH_OWN_ENABLED` проверяет наш JWT и возвращает `AuthUser(user_id, email)`; хелперы `get_or_create_user_by_oauth`, `audit_log`, вызовы аудита в ключевых эндпоинтах.

---

## Вариант 2: Внешний сервис авторизации (asudd/services/auth)

Сервис авторизации развёрнут в GitLab: [gitlab.ripas.ru/asudd/services/auth](https://gitlab.ripas.ru/asudd/services/auth).

## Как устроен auth-сервис

- **Стек:** FastAPI, JWT (HS256), Redis (токены и конфиг), PostgreSQL (пользователи и права).
- **Базовый путь API:** `/api/v1/auth`.
- **Основные эндпоинты:**
  - **POST `/api/v1/auth/login`** — вход. Form: `username`, `password`; заголовок `sid` (идентификатор сессии). Ответ: `access_token`, `refresh_token` (JWT).
  - **GET `/api/v1/auth/check`** — проверка доступа к URL. Заголовки: `Authorization: Bearer <access_token>`, `X-Original-URI` (или `X-Checked-URI`) — путь для проверки. Ответ: 200 — доступ разрешён, 401 — не авторизован, 403 — доступ запрещён.
  - **GET `/api/v1/auth/me`** — данные текущего пользователя. Заголовок: `Authorization: Bearer <access_token>`.
  - **GET `/api/v1/auth/logout`** — выход (инвалидация токена).

- **Права доступа:** в БД auth хранятся правила по путям (Unix wildcards, fnmatch). Проверка в `/check`: по пути и пользователю из токена возвращается разрешён/запрещён.

- **Внешние пользователи:** поддерживаются JWT с подписью RS256 (ключ задаётся в auth), в payload: `user_name`, `exp`, `jti`.

## Применение в tg_digest_system

1. **Единый вход:** пользователь логинится в auth-сервисе (логин/пароль или внешний IdP), получает `access_token`. Наш веб-интерфейс и API принимают только этот токен (в заголовке `Authorization`).
2. **Проверка доступа:** перед обработкой запроса к защищённому маршруту вызываем **GET** `AUTH_SERVICE_URL/api/v1/auth/check` с заголовками `Authorization: Bearer <token>` и `X-Original-URI: <запрашиваемый путь>`. 200 — продолжаем, 401/403 — возвращаем клиенту.
3. **Идентификация пользователя:** из токена (после проверки через auth) или через **GET** `/api/v1/auth/me` получаем `login` (username). В нашей БД храним привязку `auth_login` ↔ `telegram_id` (таблица `users` или отдельная), чтобы знать, кому показывать каналы и куда слать дайджесты.

## Переменные окружения (наш проект)

- **AUTH_SERVICE_URL** — базовый URL auth-сервиса (например `http://auth:8082` или `https://auth.ripas.ru`).
- **AUTH_CHECK_ENABLED** — `1` или `true` для включения проверки через auth; иначе маршруты работают без проверки токена.

## Варианты размещения

- **Вариант A:** nginx перед нашим приложением: `auth_request /api/v1/auth/check`; проксирование `Authorization` и `X-Original-URI` на auth. Наш backend тогда может не вызывать auth сам — nginx уже отфильтровал неавторизованных.
- **Вариант B:** наш backend сам вызывает auth `/check` (или `/me`) в middleware/dependency при каждом запросе к `/api/*` и к страницам типа `/channels`, `/prompts`. Токен брать из `Authorization` или из cookie (если фронт кладёт токен в cookie после логина через auth).

## Реализация в коде

- **`web/auth_client.py`** — клиент auth-сервиса: `login()`, `check_token()`, `get_username()`, `token_from_header()`. Переменные окружения: `AUTH_SERVICE_URL`, `AUTH_CHECK_ENABLED`.
- **`web_api.py`**:
  - Зависимость **`get_current_auth_user`**: при включённой проверке извлекает токен из заголовка `Authorization` или из cookie `auth_token`, вызывает auth `/check` и возвращает login пользователя; для API возвращает 401, для HTML-страниц — редирект на `/login?next=...`.
  - **GET /login** — страница входа: при `AUTH_CHECK_ENABLED` отображается форма логин/пароль (`login_auth.html`), иначе — вход по Telegram ID (`login.html`).
  - **POST /login** — при включённом auth: приём `username`, `password`, вызов auth `/login`, установка cookie `auth_token` (HttpOnly, 1 ч), редирект на `next` или `/`.
  - **GET /logout** — удаление cookie `auth_token`, редирект на `/`.

Защищённые маршруты (при включённой авторизации — своей или внешней): `/`, `/channels`, `/prompts`, `/instructions`, все `/api/*` кроме `/api/check-chat`, `/api/check-recipient`, `/health`. Публичные: `/login`, `/logout`, `/auth/yandex`, `/auth/yandex/callback`, `/api/check-chat`, `/api/check-recipient`, `/health`.

Приоритет: если **AUTH_OWN_ENABLED=1**, используется наша проверка JWT и при необходимости OAuth; иначе при **AUTH_CHECK_ENABLED=1** — внешний auth-сервис. Если оба выключены, `get_current_auth_user` возвращает `None` и проверка не выполняется.
