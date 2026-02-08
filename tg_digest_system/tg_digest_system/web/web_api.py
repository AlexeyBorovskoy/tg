#!/usr/bin/env python3
"""
web_api.py — FastAPI веб-приложение для управления каналами
Секреты и ключи API загружаются из secrets.env (см. secrets.env.example).
"""

import os
import json
import secrets
from pathlib import Path
from urllib.parse import quote

# Загрузка секретов из secrets.env (доступно в корне репо и в docker/)
for _path in (
    os.environ.get("SECRETS_ENV"),
    Path(__file__).resolve().parent.parent / "docker" / "secrets.env",
    Path(__file__).resolve().parent.parent / "secrets.env",
    Path.cwd() / "secrets.env",
    Path.cwd() / "docker" / "secrets.env",
):
    if _path and Path(_path).exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(_path, override=False)
            break
        except ImportError:
            break

import logging
import asyncio
from typing import Optional, List
from datetime import datetime

from fastapi import FastAPI, HTTPException, Depends, Request, Form, Header
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import psycopg2
from psycopg2.extras import RealDictCursor, Json

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="TG Digest Web Interface", version="1.0.0")

# Пути
BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Создаём директории если их нет
TEMPLATES_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Интеграция: своя авторизация (OAuth + JWT) или внешний auth-сервис
try:
    from auth_own import (
        AUTH_OWN_ENABLED,
        AuthUser,
        create_access_token,
        verify_access_token,
        get_google_authorize_url,
        get_yandex_authorize_url,
        exchange_google_code,
        exchange_yandex_code,
        token_from_header as auth_own_token_from_header,
        GOOGLE_CLIENT_ID,
        YANDEX_CLIENT_ID,
    )
except ImportError:
    AUTH_OWN_ENABLED = False
    AuthUser = None
    create_access_token = None
    verify_access_token = None
    get_google_authorize_url = None
    get_yandex_authorize_url = None
    exchange_google_code = None
    exchange_yandex_code = None
    auth_own_token_from_header = None
    GOOGLE_CLIENT_ID = ""
    YANDEX_CLIENT_ID = ""

try:
    from auth_client import (
        AUTH_CHECK_ENABLED,
        AUTH_SERVICE_URL,
        check_token as auth_check_token,
        token_from_header as auth_token_from_header,
        login as auth_login,
    )
except ImportError:
    AUTH_CHECK_ENABLED = False
    AUTH_SERVICE_URL = ""
    auth_check_token = None
    auth_token_from_header = None
    auth_login = None

# Включена ли какая-либо проверка авторизации
AUTH_REQUIRED = AUTH_OWN_ENABLED or (AUTH_CHECK_ENABLED and AUTH_SERVICE_URL)

# Имя cookie с access_token
AUTH_COOKIE_NAME = "auth_token"


def _is_api_request(request: Request) -> bool:
    return request.url.path.startswith("/api/") or "application/json" in (request.headers.get("accept") or "")


async def get_current_auth_user(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """
    Если включена наша авторизация (OAuth + JWT) — проверяет наш токен и возвращает AuthUser.
    Иначе если включён внешний auth — проверяет через auth-сервис и возвращает email (str).
    Иначе возвращает None. Для HTML без токена — RedirectResponse на /login.
    """
    if not AUTH_REQUIRED:
        return None
    token = None
    if authorization:
        if auth_own_token_from_header:
            token = auth_own_token_from_header(authorization)
        if not token and auth_token_from_header:
            token = auth_token_from_header(authorization)
    if not token and request.cookies:
        token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        if _is_api_request(request):
            raise HTTPException(status_code=401, detail="Требуется авторизация")
        next_path = request.url.path
        if request.query_params:
            next_path = next_path + "?" + str(request.query_params)
        return RedirectResponse(url=f"/login?next={quote(next_path, safe='')}", status_code=302)

    # Сначала проверяем наш JWT
    if AUTH_OWN_ENABLED and verify_access_token and token:
        auth_user = verify_access_token(token)
        if auth_user:
            return auth_user
        if AUTH_OWN_ENABLED and not AUTH_CHECK_ENABLED:
            if _is_api_request(request):
                raise HTTPException(status_code=401, detail="Недействительный токен")
            return RedirectResponse(url="/login?next=" + quote(request.url.path, safe=""), status_code=302)

    # Внешний auth-сервис
    if AUTH_CHECK_ENABLED and AUTH_SERVICE_URL and auth_check_token:
        allowed, username = await auth_check_token(token, request.url.path)
        if allowed and username:
            return username
    if _is_api_request(request):
        raise HTTPException(status_code=401, detail="Недействительный токен или доступ запрещён")
    return RedirectResponse(url="/login?next=" + quote(request.url.path, safe=""), status_code=302)


# Подключение к БД
def get_db():
    """Получает подключение к БД"""
    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5432")),
        database=os.environ.get("PGDATABASE", "tg_digest"),
        user=os.environ.get("PGUSER", "tg_digest"),
        password=os.environ.get("PGPASSWORD", ""),
    )
    try:
        yield conn
    finally:
        conn.close()


# Pydantic модели
class ChannelCreate(BaseModel):
    telegram_chat_id: int = Field(..., description="Telegram ID чата")
    name: Optional[str] = Field(None, description="Название чата")
    recipient_telegram_id: int = Field(..., description="Telegram ID получателя дайджестов")
    recipient_name: Optional[str] = Field(None, description="Имя получателя")
    prompt_file: str = Field("prompts/digest_management.md", description="Файл промпта")
    poll_interval_minutes: int = Field(60, description="Интервал опроса в минутах")


class ChannelResponse(BaseModel):
    id: int
    telegram_chat_id: int
    name: str
    description: Optional[str]
    peer_type: str
    enabled: bool
    recipient_telegram_id: int
    recipient_name: Optional[str]
    created_at: datetime
    last_digest_at: Optional[datetime] = None
    total_messages: int = 0


class UserCreate(BaseModel):
    telegram_id: int = Field(..., description="Telegram ID пользователя")
    name: Optional[str] = None


# Вспомогательные функции
def get_or_create_user_by_oauth(
    conn,
    provider: str,
    external_id: str,
    email: str,
    display_name: Optional[str] = None,
) -> int:
    """По OAuth-провайдеру и external_id находит или создаёт пользователя. Возвращает user_id."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT user_id FROM user_identities WHERE provider = %s AND external_id = %s",
            (provider, external_id),
        )
        row = cur.fetchone()
        if row:
            return row["user_id"]
        # Создаём пользователя (telegram_id = NULL для OAuth-only)
        cur.execute(
            """INSERT INTO users (telegram_id, name, email, is_active)
               VALUES (NULL, %s, %s, true)
               RETURNING id""",
            (display_name or email, email or None),
        )
        user_id = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO user_identities (user_id, provider, external_id, email, display_name)
               VALUES (%s, %s, %s, %s, %s)""",
            (user_id, provider, external_id, email or None, display_name),
        )
        conn.commit()
        return user_id


def _audit_user_id(current_user) -> Optional[int]:
    """Из текущего пользователя (AuthUser или str) извлекает user_id для аудита."""
    if current_user is None:
        return None
    return getattr(current_user, "user_id", None)


def audit_log(
    conn,
    user_id: Optional[int],
    action: str,
    details: Optional[dict] = None,
    request: Optional[Request] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
) -> None:
    """Пишет запись в audit_log (кто и что делал)."""
    try:
        ip = None
        user_agent = None
        if request:
            ip = request.client.host if request.client else None
            user_agent = request.headers.get("user-agent")
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO audit_log (user_id, action, details, ip, user_agent, resource_type, resource_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    user_id,
                    action,
                    Json(details or {}),
                    ip,
                    user_agent,
                    resource_type,
                    resource_id,
                ),
            )
        conn.commit()
    except Exception as e:
        logger.warning("audit_log failed: %s", e)
        if conn:
            conn.rollback()


def get_or_create_user(conn, telegram_id: int, name: Optional[str] = None) -> int:
    """Получает или создаёт пользователя"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Проверяем существование
        cur.execute("SELECT id FROM users WHERE telegram_id = %s", (telegram_id,))
        row = cur.fetchone()
        if row:
            return row['id']
        
        # Создаём нового
        cur.execute(
            "INSERT INTO users (telegram_id, name) VALUES (%s, %s) RETURNING id",
            (telegram_id, name or f"User {telegram_id}")
        )
        user_id = cur.fetchone()['id']
        conn.commit()
        return user_id


async def check_chat_access(chat_id: int) -> tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """
    Проверяет наличие и доступ к чату/каналу через системную Telethon сессию.
    Возвращает (доступ_есть, peer_type, название, сообщение_об_ошибке).
    Система должна быть участником чата/канала.
    """
    try:
        from telethon import TelegramClient
        from telethon.tl.types import Channel, Chat, User
        from telethon.errors import (
            UsernameNotOccupiedError, ChannelPrivateError,
            ChannelInvalidError, ChatIdInvalidError, PeerIdInvalidError,
        )
        
        api_id = int(os.environ.get("TG_API_ID", "0"))
        api_hash = os.environ.get("TG_API_HASH", "")
        session_file = os.environ.get("TG_SESSION_FILE", "")
        
        if not api_id or not api_hash or not session_file:
            logger.error("Telegram credentials не настроены")
            return False, None, None, "Сервис не настроен для проверки Telegram. Обратитесь к администратору."
        
        client = TelegramClient(session_file, api_id, api_hash)
        await client.start()
        try:
            entity = await client.get_entity(chat_id)
            
            if isinstance(entity, User):
                await client.disconnect()
                return False, None, None, (
                    f"ID {chat_id} принадлежит пользователю, а не чату/каналу. "
                    "Укажите ID канала (отрицательное число, например -1001234567890) или ID группы."
                )
            
            if isinstance(entity, Channel):
                peer_type = "channel" if entity.broadcast else "group"
            elif isinstance(entity, Chat):
                peer_type = "group"
            else:
                peer_type = "group"
            
            name = getattr(entity, 'title', None) or getattr(entity, 'first_name', 'Unknown')
            await client.disconnect()
            logger.info(f"Доступ к чату {chat_id} ({name}) подтверждён")
            return True, peer_type, name, None
            
        except ChannelPrivateError:
            await client.disconnect()
            return False, None, None, (
                f"Канал/чат с ID {chat_id} приватный. "
                "Добавьте аккаунт системы в участники канала или группы и повторите попытку."
            )
        except (UsernameNotOccupiedError, ChannelInvalidError, ChatIdInvalidError, PeerIdInvalidError):
            await client.disconnect()
            return False, None, None, (
                f"Чат или канал с ID {chat_id} не найден или недействителен. "
                "Проверьте ID (для каналов — отрицательное число, для групп — положительное)."
            )
        except ValueError:
            await client.disconnect()
            return False, None, None, (
                f"Некорректный ID чата: {chat_id}. "
                "Укажите числовой ID канала или группы (например -1001234567890 или 5228538198)."
            )
        except Exception as e:
            logger.exception(f"Ошибка проверки доступа к чату {chat_id}: {e}")
            await client.disconnect()
            return False, None, None, (
                f"Не удалось проверить доступ к чату {chat_id}. "
                "Убедитесь, что система добавлена в этот чат/канал как участник."
            )
    except Exception as e:
        logger.exception(f"Ошибка инициализации Telethon: {e}")
        return False, None, None, "Сервис проверки Telegram временно недоступен. Попробуйте позже."


async def check_recipient_access(recipient_id: int) -> tuple[bool, Optional[str]]:
    """
    Проверяет, что получатель дайджестов (пользователь или чат) существует и доступен для системы.
    Возвращает (успех, сообщение_об_ошибке).
    """
    try:
        from telethon import TelegramClient
        from telethon.tl.types import User, Channel, Chat
        from telethon.errors import (
            PeerIdInvalidError, ChannelPrivateError, UserIdInvalidError,
            ChannelInvalidError, ChatIdInvalidError,
        )
        
        api_id = int(os.environ.get("TG_API_ID", "0"))
        api_hash = os.environ.get("TG_API_HASH", "")
        session_file = os.environ.get("TG_SESSION_FILE", "")
        
        if not api_id or not api_hash or not session_file:
            return False, "Сервис не настроен для проверки Telegram."
        
        client = TelegramClient(session_file, api_id, api_hash)
        await client.start()
        try:
            entity = await client.get_entity(recipient_id)
            name = getattr(entity, 'title', None) or getattr(entity, 'first_name', None) or getattr(entity, 'username', None) or f"ID {recipient_id}"
            await client.disconnect()
            logger.info(f"Получатель {recipient_id} ({name}) доступен")
            return True, None
        except (PeerIdInvalidError, UserIdInvalidError, ChannelInvalidError, ChatIdInvalidError):
            await client.disconnect()
            return False, (
                f"Получатель с ID {recipient_id} не найден. "
                "Укажите ваш Telegram ID (узнать: @userinfobot) или ID чата/бота, куда присылать дайджесты."
            )
        except ChannelPrivateError:
            await client.disconnect()
            return False, (
                f"Чат с ID {recipient_id} приватный и недоступен для системы. "
                "Укажите личный ID пользователя или добавьте систему в чат."
            )
        except Exception as e:
            logger.warning(f"Проверка получателя {recipient_id}: {e}")
            await client.disconnect()
            return False, (
                f"Не удалось проверить получателя {recipient_id}. "
                "Убедитесь, что ID указан верно (число, например 499412926)."
            )
    except Exception as e:
        logger.exception(f"Ошибка проверки получателя: {e}")
        return False, "Сервис проверки временно недоступен. Попробуйте позже."


# API Endpoints
@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    current_user: Optional[str] = Depends(get_current_auth_user),
):
    """Главная страница с формой добавления чата"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next_url: Optional[str] = None, error_msg: Optional[str] = None):
    """Страница входа: OAuth (Google/Яндекс), auth-сервис (логин/пароль) или Telegram ID"""
    next_url = next_url or request.query_params.get("next", "/")
    if AUTH_OWN_ENABLED:
        return templates.TemplateResponse("login_oauth.html", {
            "request": request,
            "next_url": next_url,
            "next_encoded": quote(next_url, safe=""),
            "error_msg": error_msg or request.query_params.get("error"),
            "google_enabled": bool(GOOGLE_CLIENT_ID),
            "yandex_enabled": bool(YANDEX_CLIENT_ID),
        })
    if AUTH_CHECK_ENABLED:
        return templates.TemplateResponse("login_auth.html", {
            "request": request,
            "next_url": next_url,
            "error_msg": error_msg or request.query_params.get("error"),
        })
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    next_path: str = Form(default="/", alias="next"),
    db=Depends(get_db),
):
    """Вход через auth-сервис (логин/пароль): установка cookie, редирект."""
    if not AUTH_CHECK_ENABLED or not auth_login:
        return RedirectResponse(url="/login", status_code=302)
    if not username or not password:
        return RedirectResponse(
            url=f"/login?next={quote(next_path, safe='')}&error={quote('Введите логин и пароль', safe='')}",
            status_code=302,
        )
    result = await auth_login(username, password)
    if not result:
        return RedirectResponse(
            url=f"/login?next={quote(next_path, safe='')}&error={quote('Неверный логин или пароль', safe='')}",
            status_code=302,
        )
    access_token, _refresh = result
    response = RedirectResponse(url=next_path if next_path.startswith("/") else "/", status_code=302)
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=access_token,
        max_age=3600,
        path="/",
        httponly=True,
        samesite="lax",
    )
    return response


# OAuth: редирект на провайдера
@app.get("/auth/google")
async def auth_google_redirect(request: Request):
    """Редирект на Google OAuth."""
    if not AUTH_OWN_ENABLED or not get_google_authorize_url:
        raise HTTPException(status_code=404, detail="OAuth не настроен")
    from auth_own import BASE_URL
    next_path = request.query_params.get("next", "/")
    state = secrets.token_urlsafe(32)
    redirect_uri = f"{BASE_URL}/auth/google/callback"
    url = get_google_authorize_url(state, redirect_uri)
    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie("oauth_state", state, max_age=600, path="/", httponly=True, samesite="lax")
    response.set_cookie("oauth_next", next_path, max_age=600, path="/", httponly=True, samesite="lax")
    return response


@app.get("/auth/google/callback")
async def auth_google_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, db=Depends(get_db)):
    """Callback после входа через Google: обмен code на токен, создание/поиск пользователя, наш JWT, cookie."""
    if not AUTH_OWN_ENABLED or not exchange_google_code or not create_access_token:
        raise HTTPException(status_code=404, detail="OAuth не настроен")
    state_cookie = request.cookies.get("oauth_state")
    next_path = request.cookies.get("oauth_next", "/")
    if not state_cookie or state != state_cookie or not code:
        return RedirectResponse(url=f"/login?error={quote('Ошибка входа через Google', safe='')}", status_code=302)
    from auth_own import BASE_URL
    redirect_uri = f"{BASE_URL}/auth/google/callback"
    result = await exchange_google_code(code, redirect_uri)
    if not result:
        return RedirectResponse(url=f"/login?error={quote('Не удалось получить данные от Google', safe='')}", status_code=302)
    external_id, email, display_name = result
    user_id = get_or_create_user_by_oauth(db, "google", external_id, email, display_name)
    token = create_access_token(user_id, email, display_name)
    audit_log(db, user_id, "login", {"provider": "google", "email": email}, request)
    response = RedirectResponse(url=next_path if next_path.startswith("/") else "/", status_code=302)
    response.set_cookie(AUTH_COOKIE_NAME, token, max_age=3600, path="/", httponly=True, samesite="lax")
    response.delete_cookie("oauth_state", path="/")
    response.delete_cookie("oauth_next", path="/")
    return response


@app.get("/auth/yandex")
async def auth_yandex_redirect(request: Request):
    """Редирект на Yandex OAuth."""
    if not AUTH_OWN_ENABLED or not get_yandex_authorize_url:
        raise HTTPException(status_code=404, detail="OAuth не настроен")
    from auth_own import BASE_URL
    next_path = request.query_params.get("next", "/")
    state = secrets.token_urlsafe(32)
    redirect_uri = f"{BASE_URL}/auth/yandex/callback"
    url = get_yandex_authorize_url(state, redirect_uri)
    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie("oauth_state", state, max_age=600, path="/", httponly=True, samesite="lax")
    response.set_cookie("oauth_next", next_path, max_age=600, path="/", httponly=True, samesite="lax")
    return response


@app.get("/auth/yandex/callback")
async def auth_yandex_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, db=Depends(get_db)):
    """Callback после входа через Yandex."""
    if not AUTH_OWN_ENABLED or not exchange_yandex_code or not create_access_token:
        raise HTTPException(status_code=404, detail="OAuth не настроен")
    state_cookie = request.cookies.get("oauth_state")
    next_path = request.cookies.get("oauth_next", "/")
    if not state_cookie or state != state_cookie or not code:
        return RedirectResponse(url=f"/login?error={quote('Ошибка входа через Yandex', safe='')}", status_code=302)
    from auth_own import BASE_URL
    redirect_uri = f"{BASE_URL}/auth/yandex/callback"
    result = await exchange_yandex_code(code, redirect_uri)
    if not result:
        return RedirectResponse(url=f"/login?error={quote('Не удалось получить данные от Yandex', safe='')}", status_code=302)
    external_id, email, display_name = result
    user_id = get_or_create_user_by_oauth(db, "yandex", external_id, email, display_name)
    token = create_access_token(user_id, email, display_name)
    audit_log(db, user_id, "login", {"provider": "yandex", "email": email}, request)
    response = RedirectResponse(url=next_path if next_path.startswith("/") else "/", status_code=302)
    response.set_cookie(AUTH_COOKIE_NAME, token, max_age=3600, path="/", httponly=True, samesite="lax")
    response.delete_cookie("oauth_state", path="/")
    response.delete_cookie("oauth_next", path="/")
    return response


@app.get("/logout")
async def logout_page(request: Request, db=Depends(get_db)):
    """Сброс cookie авторизации и редирект на главную. Аудит выхода — по cookie до удаления."""
    user_id = None
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if AUTH_OWN_ENABLED and verify_access_token and token:
        au = verify_access_token(token)
        if au:
            user_id = au.user_id
    if user_id is not None:
        audit_log(db, user_id, "logout", {}, request)
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return response


@app.get("/channels", response_class=HTMLResponse)
async def channels_page(
    request: Request,
    current_user: Optional[str] = Depends(get_current_auth_user),
    user_telegram_id: Optional[int] = None,
):
    """Страница со списком каналов пользователя"""
    if user_telegram_id is None:
        uid_param = request.query_params.get("user_telegram_id")
        if uid_param and uid_param.isdigit():
            user_telegram_id = int(uid_param)
        else:
            remote = request.headers.get("X-Remote-User", "")
            user_telegram_id = int(remote) if remote.isdigit() else None
    return templates.TemplateResponse("channels.html", {
        "request": request,
        "user_telegram_id": user_telegram_id or ""
    })


@app.get("/instructions", response_class=HTMLResponse)
async def instructions_page(
    request: Request,
    current_user: Optional[str] = Depends(get_current_auth_user),
):
    """Страница с инструкциями для новых пользователей"""
    return templates.TemplateResponse("instructions.html", {"request": request})


@app.get("/prompts", response_class=HTMLResponse)
async def prompts_page(
    request: Request,
    current_user: Optional[str] = Depends(get_current_auth_user),
):
    """Библиотека промптов (по умолчанию) или редактор канала (если передан channel_id)."""
    channel_id = request.query_params.get("channel_id")
    remote_user = request.headers.get("X-Remote-User", "")
    context = {"request": request, "remote_user": remote_user}
    if channel_id:
        return templates.TemplateResponse("prompts_v2.html", context)
    return templates.TemplateResponse("prompts_library.html", context)


@app.get("/api/check-chat", response_class=JSONResponse)
async def api_check_chat(chat_id: str = Query(..., description="ID чата для проверки доступа")):
    """
    Проверяет наличие и доступность чата/канала для системы (по факту ввода).
    Возвращает: available, peer_type, name, message.
    """
    try:
        cid = int(chat_id.strip())
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=200,
            content={
                "available": False,
                "peer_type": None,
                "name": None,
                "message": "ID чата должен быть числом (например: -1001234567890 или 5228538198)."
            }
        )
    if cid == 0:
        return JSONResponse(
            status_code=200,
            content={
                "available": False,
                "peer_type": None,
                "name": None,
                "message": "ID чата не может быть нулём. Укажите ID канала (отрицательное число) или группы."
            }
        )
    has_access, peer_type, name, err = await check_chat_access(cid)
    if has_access:
        return {
            "available": True,
            "peer_type": peer_type,
            "name": name,
            "message": None
        }
    return JSONResponse(
        status_code=200,
        content={
            "available": False,
            "peer_type": None,
            "name": None,
            "message": err or "Чат недоступен. Добавьте систему в канал/группу или укажите другой ID."
        }
    )


@app.get("/api/check-recipient", response_class=JSONResponse)
async def api_check_recipient(recipient_id: str = Query(..., description="ID получателя дайджестов")):
    """
    Проверяет доступность получателя для системы (по факту ввода).
    Возвращает: available, message.
    """
    try:
        rid = int(recipient_id.strip())
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=200,
            content={
                "available": False,
                "message": "ID получателя должен быть числом (например: 499412926)."
            }
        )
    if rid == 0:
        return JSONResponse(
            status_code=200,
            content={
                "available": False,
                "message": "ID получателя не может быть нулём. Укажите ваш ID или ID бота."
            }
        )
    ok, err = await check_recipient_access(rid)
    if ok:
        return {"available": True, "message": None}
    return JSONResponse(
        status_code=200,
        content={"available": False, "message": err or "Получатель недоступен. Укажите другой ID."}
    )


@app.post("/api/users", response_class=JSONResponse)
async def create_user(
    user: UserCreate,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Создаёт или получает пользователя"""
    try:
        user_id = get_or_create_user(db, user.telegram_id, user.name)
        return {"user_id": user_id, "telegram_id": user.telegram_id}
    except Exception as e:
        logger.error(f"Ошибка создания пользователя: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channels", response_class=JSONResponse)
async def list_channels(
    user_telegram_id: int,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Возвращает список каналов пользователя"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            # Получаем каналы
            cur.execute("""
                SELECT 
                    wc.*,
                    (SELECT COUNT(*) FROM tg.messages m 
                     WHERE m.user_id = wc.user_id 
                     AND m.peer_id = wc.telegram_chat_id) as total_messages,
                    (SELECT MAX(created_at) FROM rpt.digests d 
                     WHERE d.user_id = wc.user_id 
                     AND d.peer_id = wc.telegram_chat_id) as last_digest_at
                FROM web_channels wc
                WHERE wc.user_id = %s
                ORDER BY wc.created_at DESC
            """, (user_id,))
            
            channels = cur.fetchall()
            
            return {
                "channels": [
                    {
                        "id": ch['id'],
                        "telegram_chat_id": ch['telegram_chat_id'],
                        "name": ch['name'],
                        "description": ch['description'],
                        "peer_type": ch['peer_type'],
                        "enabled": ch['enabled'],
                        "recipient_telegram_id": ch['recipient_telegram_id'],
                        "recipient_name": ch['recipient_name'],
                        "created_at": ch['created_at'].isoformat() if ch['created_at'] else None,
                        "last_digest_at": ch['last_digest_at'].isoformat() if ch['last_digest_at'] else None,
                        "total_messages": ch['total_messages'] or 0,
                        "access_method": ch.get('access_method', 'system_session'),
                        "access_status": ch.get('access_status', 'available'),
                        "consolidated_doc_path": ch.get('consolidated_doc_path'),
                    }
                    for ch in channels
                ]
            }
    except Exception as e:
        logger.error(f"Ошибка получения каналов: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _validate_channel_params(
    user_telegram_id: Optional[str],
    telegram_chat_id: Optional[str],
    recipient_telegram_id: Optional[str],
) -> List[dict]:
    """Проверка параметров добавления канала. Возвращает список ошибок [{field, message}]."""
    errors = []
    if not user_telegram_id or not str(user_telegram_id).strip():
        errors.append({"field": "user_telegram_id", "message": "Укажите ваш Telegram ID (число). Узнать можно через @userinfobot."})
    else:
        try:
            uid = int(user_telegram_id)
            if uid == 0:
                errors.append({"field": "user_telegram_id", "message": "Telegram ID не может быть нулём. Укажите ваш реальный ID."})
        except (TypeError, ValueError):
            errors.append({"field": "user_telegram_id", "message": "Telegram ID должен быть числом (например: 499412926)."})

    if not telegram_chat_id or not str(telegram_chat_id).strip():
        errors.append({"field": "telegram_chat_id", "message": "Укажите Telegram ID чата для мониторинга (канал или группа)."})
    else:
        try:
            cid = int(telegram_chat_id)
            if cid == 0:
                errors.append({"field": "telegram_chat_id", "message": "ID чата не может быть нулём. Укажите ID канала (отрицательное число) или группы."})
        except (TypeError, ValueError):
            errors.append({"field": "telegram_chat_id", "message": "ID чата должен быть числом (например: -1001234567890 или 5228538198)."})

    if not recipient_telegram_id or not str(recipient_telegram_id).strip():
        errors.append({"field": "recipient_telegram_id", "message": "Укажите Telegram ID получателя дайджестов (куда присылать)."})
    else:
        try:
            rid = int(recipient_telegram_id)
            if rid == 0:
                errors.append({"field": "recipient_telegram_id", "message": "ID получателя не может быть нулём. Укажите ваш ID или ID бота."})
        except (TypeError, ValueError):
            errors.append({"field": "recipient_telegram_id", "message": "ID получателя должен быть числом (например: 499412926)."})

    return errors


@app.post("/api/channels", response_class=JSONResponse)
async def create_channel(
    request: Request,
    user_telegram_id: str = Form(..., description="Telegram ID пользователя"),
    telegram_chat_id: str = Form(..., description="ID чата для мониторинга"),
    name: Optional[str] = Form(None),
    recipient_telegram_id: str = Form(..., description="ID получателя дайджестов"),
    recipient_name: Optional[str] = Form(None),
    prompt_file: str = Form("prompts/digest_management.md"),
    poll_interval_minutes: int = Form(60),
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Добавляет новый канал для пользователя. Перед добавлением проверяет все параметры и доступность чата."""
    # 1. Проверка формата и обязательности полей
    validation_errors = _validate_channel_params(user_telegram_id, telegram_chat_id, recipient_telegram_id)
    if validation_errors:
        return JSONResponse(
            status_code=400,
            content={"success": False, "errors": validation_errors, "message": "Исправьте указанные поля и отправьте форму снова."}
        )

    uid = int(user_telegram_id)
    chat_id = int(telegram_chat_id)
    recip_id = int(recipient_telegram_id)

    try:
        # 2. Получаем или создаём пользователя
        user_id = get_or_create_user(db, uid, None)

        # 3. Проверяем наличие и доступ к чату для мониторинга
        has_access, peer_type, chat_name, chat_err = await check_chat_access(chat_id)
        errors = []
        if not has_access:
            errors.append({
                "field": "telegram_chat_id",
                "message": chat_err or (
                    f"Система не имеет доступа к чату с ID {chat_id}. "
                    "Добавьте аккаунт системы в канал/группу или укажите другой ID чата."
                )
            })

        # 4. Проверяем доступность получателя дайджестов
        recip_ok, recip_err = await check_recipient_access(recip_id)
        if not recip_ok:
            errors.append({
                "field": "recipient_telegram_id",
                "message": recip_err or (
                    f"Получатель с ID {recip_id} недоступен. "
                    "Укажите ваш Telegram ID или ID чата/бота, куда отправлять дайджесты."
                )
            })

        if errors:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "errors": errors,
                    "message": "Исправьте указанные поля и отправьте форму снова."
                }
            )

        # Оба проверки пройдены — создаём канал
        access_method = "system_session"
        access_status = "available"
        final_name = (name or "").strip() or chat_name or f"Chat {chat_id}"
        
        # Формируем путь к сводному документу
        doc_name = final_name.lower().replace(' ', '_').replace('/', '_')
        doc_name = ''.join(c for c in doc_name if c.isalnum() or c in '_-')
        consolidated_doc_path = f"docs/reference/{doc_name}.md"
        
        # Создаём запись в БД
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO web_channels (
                    user_id, telegram_chat_id, name, description, peer_type,
                    prompt_file, consolidated_doc_path, consolidated_doc_prompt_file,
                    poll_interval_minutes, enabled, recipient_telegram_id, recipient_name,
                    access_method, access_status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                user_id,
                chat_id,
                final_name,
                "Добавлен через веб-интерфейс",
                peer_type,
                prompt_file,
                consolidated_doc_path,
                "prompts/consolidated_engineering.md",
                poll_interval_minutes,
                True,
                recip_id,
                (recipient_name or "").strip() or f"User {recip_id}",
                access_method,
                access_status
            ))
            
            channel_id = cur.fetchone()['id']
            
            # Сохраняем дефолтные промпты в channel_prompts (все данные в БД)
            prompts_dir = Path(os.environ.get("PROMPTS_DIR", "/app/prompts"))
            def _read_prompt_file(rel_path: str) -> str:
                if not rel_path:
                    return ""
                p = prompts_dir / Path(rel_path).name
                if p.exists():
                    try:
                        return p.read_text(encoding="utf-8")
                    except Exception as e:
                        logger.warning(f"Не удалось прочитать промпт из {p}: {e}")
                return ""
            digest_text = _read_prompt_file(prompt_file)
            consolidated_text = _read_prompt_file("prompts/consolidated_engineering.md")
            cur.execute("""
                INSERT INTO channel_prompts (channel_id, user_id, prompt_type, name, text, is_default)
                VALUES (%s, %s, 'digest', 'Промпт для дайджестов', %s, true),
                       (%s, %s, 'consolidated', 'Промпт для сводного документа', %s, true)
            """, (channel_id, user_id, digest_text, channel_id, user_id, consolidated_text))
            
            db.commit()
        
        audit_log(
            db, _audit_user_id(current_user), "channel_created",
            {"telegram_chat_id": chat_id, "name": final_name}, request,
            "channel", str(channel_id),
        )
        
        # Запускаем фоновую задачу загрузки истории
        message = f"Канал {final_name} добавлен. История будет загружена автоматически."
        
        return {
            "success": True,
            "channel_id": channel_id,
            "message": f"Канал {final_name} добавлен. История будет загружена автоматически.",
            "access_method": access_method,
            "access_status": access_status
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка добавления канала: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


class ChannelUpdate(BaseModel):
    """Обновление канала (только переданные поля)."""
    name: Optional[str] = None
    recipient_telegram_id: Optional[int] = None
    recipient_name: Optional[str] = None


@app.put("/api/channels/{channel_id}", response_class=JSONResponse)
async def update_channel(
    request: Request,
    channel_id: int,
    user_telegram_id: int,
    name: Optional[str] = Form(None),
    recipient_telegram_id: Optional[int] = Form(None),
    recipient_name: Optional[str] = Form(None),
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Обновляет канал пользователя (название, получатель дайджестов)."""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        if name == "":
            name = None
        if recipient_name == "":
            recipient_name = None
        
        updates = []
        params = []
        if name is not None:
            updates.append("name = %s")
            params.append(name)
        if recipient_telegram_id is not None:
            updates.append("recipient_telegram_id = %s")
            params.append(recipient_telegram_id)
        if recipient_name is not None:
            updates.append("recipient_name = %s")
            params.append(recipient_name)
        
        if not updates:
            raise HTTPException(status_code=400, detail="Не указаны поля для обновления")
        
        params.extend([channel_id, user_id])
        
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"""
                UPDATE web_channels 
                SET {", ".join(updates)}, updated_at = now()
                WHERE id = %s AND user_id = %s
                RETURNING id, name, recipient_telegram_id, recipient_name
            """, tuple(params))
            
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Канал не найден")
            
            db.commit()
        
        audit_log(
            db, _audit_user_id(current_user), "channel_updated",
            {"name": row["name"], "recipient_telegram_id": row["recipient_telegram_id"]}, request,
            "channel", str(channel_id),
        )
        return {
            "success": True,
            "message": "Канал обновлён",
            "channel": {
                "id": row["id"],
                "name": row["name"],
                "recipient_telegram_id": row["recipient_telegram_id"],
                "recipient_name": row["recipient_name"],
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка обновления канала: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channels/{channel_id}", response_class=JSONResponse)
async def delete_channel(
    request: Request,
    channel_id: int,
    user_telegram_id: int,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Удаляет канал пользователя"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        with db.cursor() as cur:
            cur.execute("""
                DELETE FROM web_channels 
                WHERE id = %s AND user_id = %s
            """, (channel_id, user_id))
            
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Канал не найден")
            
            db.commit()
        
        audit_log(
            db, _audit_user_id(current_user), "channel_deleted",
            {"channel_id": channel_id}, request, "channel", str(channel_id),
        )
        return {"success": True, "message": "Канал удалён"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка удаления канала: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/digests/{channel_id}", response_class=JSONResponse)
async def get_digests(
    channel_id: int,
    user_telegram_id: int,
    limit: int = 10,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Возвращает последние дайджесты канала"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            # Проверяем что канал принадлежит пользователю
            cur.execute("""
                SELECT telegram_chat_id FROM web_channels 
                WHERE id = %s AND user_id = %s
            """, (channel_id, user_id))
            
            channel = cur.fetchone()
            if not channel:
                raise HTTPException(status_code=404, detail="Канал не найден")
            
            # Получаем дайджесты
            cur.execute("""
                SELECT id, peer_id, msg_id_from, msg_id_to, digest_llm, created_at
                FROM rpt.digests
                WHERE user_id = %s AND peer_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (user_id, channel['telegram_chat_id'], limit))
            
            digests = cur.fetchall()
            
            return {
                "digests": [
                    {
                        "id": d['id'],
                        "msg_id_from": d['msg_id_from'],
                        "msg_id_to": d['msg_id_to'],
                        "digest_preview": (d['digest_llm'] or "")[:200] + "..." if d['digest_llm'] else None,
                        "created_at": d['created_at'].isoformat() if d['created_at'] else None,
                    }
                    for d in digests
                ]
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения дайджестов: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channels/{channel_id}/document", response_class=FileResponse)
async def get_consolidated_document(
    channel_id: int,
    user_telegram_id: int,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Возвращает сводный инженерный документ канала"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            # Проверяем что канал принадлежит пользователю
            cur.execute("""
                SELECT consolidated_doc_path, telegram_chat_id 
                FROM web_channels 
                WHERE id = %s AND user_id = %s
            """, (channel_id, user_id))
            
            channel = cur.fetchone()
            if not channel:
                raise HTTPException(status_code=404, detail="Канал не найден")
            
            doc_path = channel.get('consolidated_doc_path')
            if not doc_path:
                raise HTTPException(status_code=404, detail="Сводный документ не создан")
            
            # Получаем путь к репозиторию из env
            repo_dir = Path(os.environ.get("REPO_DIR", "/app"))
            full_path = repo_dir / doc_path
            
            if not full_path.exists():
                raise HTTPException(status_code=404, detail="Файл документа не найден")
            
            return FileResponse(
                path=str(full_path),
                filename=full_path.name,
                media_type="text/markdown"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения документа: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/prompts-library", response_class=JSONResponse)
async def get_prompts_library(
    user_telegram_id: int,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Возвращает все каналы пользователя с их промптами (библиотека промптов)."""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, name, telegram_chat_id
                FROM web_channels
                WHERE user_id = %s
                ORDER BY name
            """, (user_id,))
            channels = cur.fetchall()
            
            result = []
            for ch in channels:
                cur.execute("""
                    SELECT id, prompt_type, name, substring("text" from 1 for 200) as text_preview, is_default, created_at
                    FROM channel_prompts
                    WHERE channel_id = %s
                    ORDER BY prompt_type, is_default DESC, created_at
                """, (ch['id'],))
                prompts = cur.fetchall()
                digest_prompts = [p for p in prompts if p['prompt_type'] == 'digest']
                consolidated_prompts = [p for p in prompts if p['prompt_type'] == 'consolidated']
                result.append({
                    "id": ch['id'],
                    "name": ch['name'],
                    "telegram_chat_id": ch['telegram_chat_id'],
                    "prompts": {
                        "digest": [{"id": p["id"], "name": p["name"], "text_preview": (p["text_preview"] or "")[:150], "is_default": p["is_default"]} for p in digest_prompts],
                        "consolidated": [{"id": p["id"], "name": p["name"], "text_preview": (p["text_preview"] or "")[:150], "is_default": p["is_default"]} for p in consolidated_prompts],
                    }
                })
            
            return {"channels": result}
    except Exception as e:
        logger.error(f"Ошибка получения библиотеки промптов: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/prompt-library/templates", response_class=JSONResponse)
async def get_prompt_library_templates(
    prompt_type: Optional[str] = None,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Возвращает шаблоны промптов из таблицы prompt_library (библиотека в БД)."""
    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            if prompt_type:
                cur.execute("""
                    SELECT id, name, prompt_type, file_path, body, created_at, updated_at
                    FROM prompt_library
                    WHERE prompt_type = %s
                    ORDER BY name
                """, (prompt_type,))
            else:
                cur.execute("""
                    SELECT id, name, prompt_type, file_path, body, created_at, updated_at
                    FROM prompt_library
                    ORDER BY prompt_type, name
                """)
            rows = cur.fetchall()
            result = []
            for r in rows:
                result.append({
                    "id": r["id"],
                    "name": r["name"],
                    "prompt_type": r["prompt_type"],
                    "file_path": r.get("file_path"),
                    "body": r.get("body") or "",
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                    "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
                })
            return {"templates": result}
    except psycopg2.ProgrammingError as e:
        if "does not exist" in str(e).lower() or "relation" in str(e).lower():
            return {"templates": [], "message": "Таблица prompt_library не найдена. Выполните миграцию 005_prompt_library.sql."}
        raise
    except Exception as e:
        logger.error(f"Ошибка получения шаблонов из prompt_library: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/prompt-library/sync", response_class=JSONResponse)
async def sync_prompt_library(
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Синхронизирует шаблоны из папки PROMPTS_DIR в таблицу prompt_library."""
    prompts_dir = Path(os.environ.get("PROMPTS_DIR", "/app/prompts"))
    if not prompts_dir.exists():
        raise HTTPException(status_code=400, detail=f"Папка промптов не найдена: {prompts_dir}")
    synced = []
    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            for ext in ("*.md", "*.txt"):
                for p in prompts_dir.glob(ext):
                    rel = f"prompts/{p.name}"
                    try:
                        body = p.read_text(encoding="utf-8")
                    except Exception as e:
                        logger.warning(f"Не удалось прочитать {p}: {e}")
                        continue
                    prompt_type = "consolidated" if "consolidated" in p.name.lower() else "digest"
                    name = p.stem.replace("_", " ").replace("-", " ").title()
                    cur.execute("""
                        INSERT INTO prompt_library (name, prompt_type, file_path, body)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (file_path) DO UPDATE SET
                            name = EXCLUDED.name,
                            prompt_type = EXCLUDED.prompt_type,
                            body = EXCLUDED.body,
                            updated_at = now()
                    """, (name, prompt_type, rel, body))
                    synced.append({"file_path": rel, "name": name, "prompt_type": prompt_type})
        db.commit()
        return {"synced": len(synced), "templates": synced}
    except psycopg2.ProgrammingError as e:
        if "does not exist" in str(e).lower():
            raise HTTPException(
                status_code=400,
                detail="Таблица prompt_library не найдена. Выполните миграцию 005_prompt_library.sql."
            )
        raise
    except Exception as e:
        logger.exception(f"Ошибка синхронизации prompt_library: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channels/{channel_id}/prompts", response_class=JSONResponse)
async def get_channel_prompts(
    channel_id: int,
    user_telegram_id: int,
    prompt_type: Optional[str] = None,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Возвращает промпты канала для редактирования"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            # Проверяем что канал принадлежит пользователю
            cur.execute("""
                SELECT id FROM web_channels 
                WHERE id = %s AND user_id = %s
            """, (channel_id, user_id))
            
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Канал не найден")
            
            # Загружаем промпты из таблицы channel_prompts
            if prompt_type:
                cur.execute("""
                    SELECT id, prompt_type, name, text, is_default, created_at, updated_at
                    FROM channel_prompts
                    WHERE channel_id = %s AND prompt_type = %s
                    ORDER BY is_default DESC, created_at DESC
                """, (channel_id, prompt_type))
            else:
                cur.execute("""
                    SELECT id, prompt_type, name, text, is_default, created_at, updated_at
                    FROM channel_prompts
                    WHERE channel_id = %s
                    ORDER BY prompt_type, is_default DESC, created_at DESC
                """, (channel_id,))
            
            prompts_list = cur.fetchall()
            
            # Если промптов нет, возвращаем дефолтные из web_channels
            if not prompts_list:
                cur.execute("""
                    SELECT prompt_file, prompt_text, consolidated_doc_prompt_file, consolidated_doc_prompt_text
                    FROM web_channels
                    WHERE id = %s
                """, (channel_id,))
                channel = cur.fetchone()
                
                if channel:
                    # Загружаем из файлов если нужно
                    prompt_text = channel.get('prompt_text')
                    consolidated_prompt_text = channel.get('consolidated_doc_prompt_text')
                    
                    if not prompt_text and channel.get('prompt_file'):
                        prompt_path = Path(os.environ.get("PROMPTS_DIR", "/app/prompts")) / Path(channel['prompt_file']).name
                        if prompt_path.exists():
                            try:
                                prompt_text = prompt_path.read_text(encoding="utf-8")
                            except Exception as e:
                                logger.warning(f"Не удалось загрузить промпт из файла {prompt_path}: {e}")
                    
                    if not consolidated_prompt_text and channel.get('consolidated_doc_prompt_file'):
                        cons_prompt_path = Path(os.environ.get("PROMPTS_DIR", "/app/prompts")) / Path(channel['consolidated_doc_prompt_file']).name
                        if cons_prompt_path.exists():
                            try:
                                consolidated_prompt_text = cons_prompt_path.read_text(encoding="utf-8")
                            except Exception as e:
                                logger.warning(f"Не удалось загрузить промпт сводного документа из файла {cons_prompt_path}: {e}")
                    
                    return {
                        "prompts": {
                            "digest": [{
                                "id": "default_digest",
                                "name": "Промпт для дайджестов",
                                "text": prompt_text or "",
                                "is_default": True
                            }],
                            "consolidated": [{
                                "id": "default_consolidated",
                                "name": "Промпт для сводного документа",
                                "text": consolidated_prompt_text or "",
                                "is_default": True
                            }]
                        }
                    }
            
            # Группируем промпты по типам
            prompts_by_type = {"digest": [], "consolidated": []}
            for p in prompts_list:
                prompts_by_type[p['prompt_type']].append({
                    "id": p['id'],
                    "name": p['name'],
                    "text": p['text'],
                    "is_default": p['is_default'],
                    "created_at": p['created_at'].isoformat() if p['created_at'] else None,
                    "updated_at": p['updated_at'].isoformat() if p['updated_at'] else None
                })
            
            return {"prompts": prompts_by_type}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения промптов: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels/{channel_id}/prompts", response_class=JSONResponse)
async def create_channel_prompt(
    request: Request,
    channel_id: int,
    user_telegram_id: int = Form(...),
    prompt_type: str = Form(...),
    name: str = Form(...),
    text: str = Form(...),
    is_default: bool = Form(False),
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Создаёт новый промпт для канала"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        if prompt_type not in ['digest', 'consolidated']:
            raise HTTPException(status_code=400, detail="prompt_type должен быть 'digest' или 'consolidated'")
        
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            # Проверяем что канал принадлежит пользователю
            cur.execute("""
                SELECT id FROM web_channels 
                WHERE id = %s AND user_id = %s
            """, (channel_id, user_id))
            
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Канал не найден")
            
            # Если это промпт по умолчанию, снимаем флаг с других
            if is_default:
                cur.execute("""
                    UPDATE channel_prompts 
                    SET is_default = false 
                    WHERE channel_id = %s AND prompt_type = %s
                """, (channel_id, prompt_type))
            
            # Создаём новый промпт
            cur.execute("""
                INSERT INTO channel_prompts (channel_id, user_id, prompt_type, name, text, is_default)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (channel_id, user_id, prompt_type, name, text, is_default))
            
            prompt_id = cur.fetchone()['id']
            db.commit()
        
        audit_log(
            db, _audit_user_id(current_user), "prompt_created",
            {"channel_id": channel_id, "prompt_type": prompt_type}, request,
            "prompt", str(prompt_id),
        )
        return {"success": True, "prompt_id": prompt_id, "message": "Промпт создан"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка создания промпта: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/channels/{channel_id}/prompts/{prompt_id}", response_class=JSONResponse)
async def update_channel_prompt(
    request: Request,
    channel_id: int,
    prompt_id: int,
    user_telegram_id: int = Form(...),
    name: Optional[str] = Form(None),
    text: Optional[str] = Form(None),
    is_default: Optional[bool] = Form(None),
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Обновляет промпт канала"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        with db.cursor() as cur:
            # Проверяем что промпт принадлежит каналу пользователя
            cur.execute("""
                SELECT cp.id, cp.prompt_type 
                FROM channel_prompts cp
                JOIN web_channels wc ON cp.channel_id = wc.id
                WHERE cp.id = %s AND cp.channel_id = %s AND wc.user_id = %s
            """, (prompt_id, channel_id, user_id))
            
            prompt = cur.fetchone()
            if not prompt:
                raise HTTPException(status_code=404, detail="Промпт не найден")
            
            # Если устанавливаем как default, снимаем флаг с других
            if is_default is True:
                cur.execute("""
                    UPDATE channel_prompts 
                    SET is_default = false 
                    WHERE channel_id = %s AND prompt_type = %s AND id != %s
                """, (channel_id, prompt[1], prompt_id))
            
            # Обновляем промпт
            update_fields = []
            params = []
            
            if name is not None:
                update_fields.append("name = %s")
                params.append(name)
            
            if text is not None:
                update_fields.append("text = %s")
                params.append(text)
            
            if is_default is not None:
                update_fields.append("is_default = %s")
                params.append(is_default)
            
            if update_fields:
                update_fields.append("updated_at = now()")
                params.extend([prompt_id, channel_id, user_id])
                
                cur.execute(f"""
                    UPDATE channel_prompts 
                    SET {', '.join(update_fields)}
                    WHERE id = %s AND channel_id = %s 
                    AND EXISTS (
                        SELECT 1 FROM web_channels wc 
                        WHERE wc.id = channel_prompts.channel_id AND wc.user_id = %s
                    )
                """, params)
                
                db.commit()
        
        audit_log(
            db, _audit_user_id(current_user), "prompt_updated",
            {"channel_id": channel_id, "prompt_id": prompt_id}, request,
            "prompt", str(prompt_id),
        )
        return {"success": True, "message": "Промпт обновлён"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка обновления промпта: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channels/{channel_id}/prompts/{prompt_id}", response_class=JSONResponse)
async def delete_channel_prompt(
    request: Request,
    channel_id: int,
    prompt_id: int,
    user_telegram_id: int,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Удаляет промпт канала"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        with db.cursor() as cur:
            # Проверяем что промпт принадлежит каналу пользователя
            cur.execute("""
                DELETE FROM channel_prompts
                WHERE id = %s AND channel_id = %s 
                AND EXISTS (
                    SELECT 1 FROM web_channels wc 
                    WHERE wc.id = channel_prompts.channel_id AND wc.user_id = %s
                )
            """, (prompt_id, channel_id, user_id))
            
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Промпт не найден")
            
            db.commit()
        
        audit_log(
            db, _audit_user_id(current_user), "prompt_deleted",
            {"channel_id": channel_id, "prompt_id": prompt_id}, request,
            "prompt", str(prompt_id),
        )
        return {"success": True, "message": "Промпт удалён"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка удаления промпта: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------------------------------------------------------
# Настройки в БД (entity_settings) — чаты, боты, пользователи, система
# -----------------------------------------------------------------------------
@app.get("/api/settings", response_class=JSONResponse)
async def get_entity_settings(
    entity_type: str,
    entity_id: int = 0,
    key: Optional[str] = None,
    user_telegram_id: Optional[int] = None,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Возвращает настройки сущности (user, channel, bot, system) из БД."""
    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            if key:
                cur.execute("""
                    SELECT id, entity_type, entity_id, key, value, updated_at
                    FROM entity_settings
                    WHERE entity_type = %s AND entity_id = %s AND key = %s
                """, (entity_type, entity_id, key))
            else:
                cur.execute("""
                    SELECT id, entity_type, entity_id, key, value, updated_at
                    FROM entity_settings
                    WHERE entity_type = %s AND entity_id = %s
                    ORDER BY key
                """, (entity_type, entity_id,))
            rows = cur.fetchall()
        out = [{"id": r["id"], "key": r["key"], "value": r["value"], "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None} for r in rows]
        if key and out:
            return out[0]
        return {"settings": out}
    except psycopg2.ProgrammingError as e:
        if "does not exist" in str(e).lower():
            return {"settings": []}
        raise
    except Exception as e:
        logger.error(f"Ошибка получения настроек: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class EntitySettingUpdate(BaseModel):
    entity_type: str
    entity_id: int = 0
    key: str
    value: Optional[dict] = None


@app.put("/api/settings", response_class=JSONResponse)
async def set_entity_setting(
    request: Request,
    body: EntitySettingUpdate,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Записывает настройку сущности в БД (чаты, боты, пользователи, system)."""
    val = body.value if body.value is not None else {}
    try:
        with db.cursor() as cur:
            cur.execute("""
                INSERT INTO entity_settings (entity_type, entity_id, key, value)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (entity_type, entity_id, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """, (body.entity_type, body.entity_id, body.key, Json(val)))
        db.commit()
        audit_log(
            db, _audit_user_id(current_user), "settings_updated",
            {"entity_type": body.entity_type, "entity_id": body.entity_id, "key": body.key}, request,
            "settings", f"{body.entity_type}:{body.entity_id}:{body.key}",
        )
        return {"success": True, "entity_type": body.entity_type, "entity_id": body.entity_id, "key": body.key}
    except psycopg2.ProgrammingError as e:
        if "does not exist" in str(e).lower():
            raise HTTPException(status_code=400, detail="Выполните миграцию 006_entity_settings_and_bots.sql.")
        raise
    except Exception as e:
        logger.error(f"Ошибка записи настройки: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health", response_class=JSONResponse)
async def health():
    """Healthcheck endpoint"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
