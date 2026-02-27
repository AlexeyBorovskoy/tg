#!/usr/bin/env python3
"""
web_api.py — FastAPI веб-приложение для управления каналами
Секреты и ключи API загружаются из secrets.env (см. secrets.env.example).
"""

import os
import json
import secrets
import hashlib
import hmac
import re
from pathlib import Path
from urllib.parse import quote, unquote

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
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Depends, Request, Form, Header, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse, Response
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
USER_SECRETS_DIR = Path(os.environ.get("USER_SECRETS_DIR", "/app/data/user-secrets"))

# Создаём директории если их нет
TEMPLATES_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Интеграция: своя авторизация (OAuth + JWT) или внешний auth-сервис
try:
    from auth_own import (
        AUTH_OWN_ENABLED,
        AuthUser,
        create_access_token,
        verify_access_token,
        get_yandex_authorize_url,
        exchange_yandex_code,
        token_from_header as auth_own_token_from_header,
        YANDEX_CLIENT_ID,
    )
except ImportError:
    AUTH_OWN_ENABLED = False
    AuthUser = None
    create_access_token = None
    verify_access_token = None
    get_yandex_authorize_url = None
    exchange_yandex_code = None
    auth_own_token_from_header = None
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

# Локальная тестовая авторизация (логин/пароль в БД + сессия)
AUTH_LOCAL_ENABLED = os.environ.get("AUTH_LOCAL_ENABLED", "0").lower() in ("1", "true", "yes")
AUTH_LOCAL_COOKIE_NAME = "session_token"
AUTH_LOCAL_SESSION_DAYS = int(os.environ.get("AUTH_LOCAL_SESSION_DAYS", "30"))
AUTH_LOCAL_MIN_PASSWORD_LEN = int(os.environ.get("AUTH_LOCAL_MIN_PASSWORD_LEN", "8"))
AUTH_LOCAL_ADMIN_LOGIN = (os.environ.get("AUTH_LOCAL_ADMIN_LOGIN", "alex") or "").strip().lower()
TELETHON_SESSION_DIR = Path(os.environ.get("TELETHON_SESSION_DIR", "/app/data/user-sessions"))

# Включена ли какая-либо проверка авторизации
AUTH_REQUIRED = AUTH_LOCAL_ENABLED or AUTH_OWN_ENABLED or (AUTH_CHECK_ENABLED and AUTH_SERVICE_URL)

# Имя cookie с access_token
AUTH_COOKIE_NAME = "auth_token"

# Пути, доступные без авторизации (весь остальной сервис закрыт идентификацией)
_PUBLIC_PATHS = ("/login", "/register", "/auth/", "/logout", "/health")


def _is_public_path(path: str) -> bool:
    if path == "/login" or path == "/register" or path == "/logout" or path == "/health":
        return True
    if path.startswith("/auth/"):
        return True
    if path.startswith("/static/"):
        return True
    return False


@dataclass
class LocalAuthUser:
    user_id: int
    login: str
    display_name: Optional[str] = None


@dataclass
class TelethonPendingAuth:
    phone: str
    phone_code_hash: str
    tg_api_id: int
    tg_api_hash: str
    tg_session_file: str
    created_at: datetime


_TELETHON_PENDING_AUTH: Dict[int, TelethonPendingAuth] = {}
TELETHON_CODE_TTL_SECONDS = int(os.environ.get("TELETHON_CODE_TTL_SECONDS", "600"))


@app.middleware("http")
async def require_auth_middleware(request: Request, call_next):
    """
    Когда включена авторизация (local/OAuth/external auth), весь сервис закрыт идентификацией:
    без валидной cookie/токена доступны только /login, /register, /auth/*, /logout, /health.
    """
    if not AUTH_REQUIRED:
        return await call_next(request)
    path = request.url.path.rstrip("/") or "/"
    if _is_public_path(path):
        return await call_next(request)
    has_cookie = bool(request.cookies.get(AUTH_COOKIE_NAME))
    has_local_cookie = bool(request.cookies.get(AUTH_LOCAL_COOKIE_NAME))
    has_bearer = (request.headers.get("authorization") or "").strip().lower().startswith("bearer ")
    if has_cookie or has_local_cookie or has_bearer:
        return await call_next(request)
    next_url = quote(request.url.path, safe="")
    if request.query_params:
        next_url = quote(request.url.path + "?" + str(request.query_params), safe="")
    return RedirectResponse(url=f"/login?next={next_url}", status_code=302)


def _is_api_request(request: Request) -> bool:
    return request.url.path.startswith("/api/") or "application/json" in (request.headers.get("accept") or "")


def _db_connect():
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5432")),
        database=os.environ.get("PGDATABASE", "tg_digest"),
        user=os.environ.get("PGUSER", "tg_digest"),
        password=os.environ.get("PGPASSWORD", ""),
    )


def _normalize_login(login: str) -> str:
    return (login or "").strip().lower()


def _is_admin_user(current_user) -> bool:
    if isinstance(current_user, LocalAuthUser):
        return _normalize_login(current_user.login) == AUTH_LOCAL_ADMIN_LOGIN
    return False


def _default_telethon_session_file(user_id: int) -> str:
    return str(TELETHON_SESSION_DIR / f"user_{user_id}.session")


def _is_valid_login(login: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9_.-]{3,64}", login))


def _is_valid_password(password: str) -> bool:
    return AUTH_LOCAL_MIN_PASSWORD_LEN <= len(password or "") <= 256


def _normalize_next_path(next_path: Optional[str]) -> str:
    p = unquote((next_path or "/").strip())
    if not p.startswith("/") or p.startswith("//"):
        return "/"
    return p


def _post_login_redirect(next_path: Optional[str]) -> str:
    p = _normalize_next_path(next_path)
    if p in ("/", "/login", "/register"):
        return "/setup"
    return p


def _oauth_callback_uri(request: Request) -> str:
    """
    Возвращает callback URI для OAuth.
    Приоритет: BASE_URL из env -> текущий host запроса.
    """
    base_url = (os.environ.get("BASE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        base_url = str(request.base_url).rstrip("/")
    return f"{base_url}/auth/yandex/callback"


def _hash_password(password: str) -> str:
    iterations = 240000
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${dk}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, iterations_s, salt, digest = stored_hash.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iterations_s)
        check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations).hex()
        return hmac.compare_digest(check, digest)
    except Exception:
        return False


def _create_local_session(conn, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(days=AUTH_LOCAL_SESSION_DAYS)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_sessions (user_id, session_token, expires_at)
            VALUES (%s, %s, %s)
            """,
            (user_id, token, expires_at),
        )
    return token


def _delete_local_session(conn, session_token: str) -> None:
    if not session_token:
        return
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_sessions WHERE session_token = %s", (session_token,))


def _get_local_user_by_session(session_token: str) -> Optional[LocalAuthUser]:
    if not session_token:
        return None
    conn = None
    try:
        conn = _db_connect()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT us.user_id, ula.login, u.name
                FROM user_sessions us
                JOIN user_local_auth ula ON ula.user_id = us.user_id
                JOIN users u ON u.id = us.user_id
                WHERE us.session_token = %s
                  AND us.expires_at > now()
                  AND ula.is_active = true
                LIMIT 1
                """,
                (session_token,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                "UPDATE user_sessions SET last_used_at = now() WHERE session_token = %s",
                (session_token,),
            )
        conn.commit()
        return LocalAuthUser(user_id=row["user_id"], login=row["login"], display_name=row.get("name"))
    except Exception as e:
        logger.warning("Local auth session check failed: %s", e)
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()


def _set_local_session_cookie(response: Response, session_token: str) -> None:
    max_age = AUTH_LOCAL_SESSION_DAYS * 24 * 60 * 60
    response.set_cookie(
        key=AUTH_LOCAL_COOKIE_NAME,
        value=session_token,
        max_age=max_age,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )


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
    # Local auth (session cookie) приоритетен в тестовом режиме.
    if AUTH_LOCAL_ENABLED:
        local_session_token = request.cookies.get(AUTH_LOCAL_COOKIE_NAME)
        if local_session_token:
            local_user = _get_local_user_by_session(local_session_token)
            if local_user:
                return local_user
            if not AUTH_OWN_ENABLED and not (AUTH_CHECK_ENABLED and AUTH_SERVICE_URL):
                if _is_api_request(request):
                    raise HTTPException(status_code=401, detail="Требуется вход по логину/паролю")
                next_path = request.url.path
                if request.query_params:
                    next_path = next_path + "?" + str(request.query_params)
                return RedirectResponse(url=f"/login?next={quote(next_path, safe='')}", status_code=302)

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


class UserRuntimeConfigUpdate(BaseModel):
    user_telegram_id: Optional[int] = Field(None, description="Telegram ID пользователя (legacy fallback)")
    tg_api_id: int = Field(..., description="Telegram API ID пользователя")
    tg_api_hash: str = Field(..., description="Telegram API HASH пользователя")
    tg_phone: Optional[str] = Field(None, description="Телефон Telegram аккаунта пользователя")
    tg_session_file: Optional[str] = Field(None, description="Путь к файлу user session")
    bot_token: Optional[str] = Field(None, description="Токен бота пользователя для рассылки")
    bot_name: Optional[str] = Field("Default Bot", description="Название бота")
    make_bot_default: bool = Field(True, description="Сделать бота дефолтным")


class TelethonSendCodeRequest(BaseModel):
    user_telegram_id: Optional[int] = Field(None, description="Telegram ID пользователя (legacy fallback)")
    tg_phone: Optional[str] = Field(None, description="Телефон пользователя в формате +7999...")


class TelethonVerifyCodeRequest(BaseModel):
    user_telegram_id: Optional[int] = Field(None, description="Telegram ID пользователя (legacy fallback)")
    code: str = Field(..., description="Код подтверждения из Telegram")
    password: Optional[str] = Field(None, description="Пароль 2FA Telegram (если включен)")


class PromptLibraryTemplateCreate(BaseModel):
    user_telegram_id: Optional[int] = Field(None, description="Telegram ID пользователя (legacy fallback)")
    name: str = Field(..., description="Название шаблона")
    prompt_type: str = Field(..., description="digest|consolidated")
    body: str = Field(..., description="Текст шаблона")
    share_to_library: bool = Field(False, description="Публиковать в общей библиотеке")


class PromptLibraryTemplateSharingUpdate(BaseModel):
    user_telegram_id: Optional[int] = Field(None, description="Telegram ID пользователя (legacy fallback)")
    share_to_library: bool = Field(..., description="true=public, false=private")


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


def list_users_with_identities(conn, audit_limit: int = 50):
    """
    Возвращает список пользователей с привязками OAuth и последние записи audit_log.
    Для страницы управления пользователями. При отсутствии таблиц миграции 007 возвращает пустые списки.
    """
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT u.id, u.telegram_id, u.name, u.email, u.is_active, u.created_at,
                       ui.provider, ui.external_id, ui.email AS identity_email, ui.display_name, ui.linked_at
                FROM users u
                LEFT JOIN user_identities ui ON ui.user_id = u.id
                ORDER BY u.id, ui.linked_at
            """)
            rows = cur.fetchall()
    except Exception:
        return {"users": [], "audit": []}
    users_by_id = {}
    for r in rows:
        uid = r["id"]
        if uid not in users_by_id:
            users_by_id[uid] = {
                "id": uid,
                "telegram_id": r["telegram_id"],
                "name": r.get("name"),
                "email": r.get("email"),
                "is_active": r.get("is_active", True),
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "identities": [],
            }
        if r.get("provider"):
            users_by_id[uid]["identities"].append({
                "provider": r["provider"],
                "email": r.get("identity_email"),
                "display_name": r.get("display_name"),
                "linked_at": r["linked_at"].isoformat() if r.get("linked_at") else None,
            })
    users_list = list(users_by_id.values())

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT al.id, al.user_id, al.action, al.at, al.details, al.ip
                FROM audit_log al
                ORDER BY al.at DESC
                LIMIT %s
            """, (audit_limit,))
            audit_rows = cur.fetchall()
    except Exception:
        audit_list = []
    else:
        audit_list = [
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "action": r["action"],
                "at": r["at"].isoformat() if r.get("at") else None,
                "details": r.get("details"),
                "ip": str(r["ip"]) if r.get("ip") else None,
            }
            for r in audit_rows
        ]
    return {"users": users_list, "audit": audit_list}


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


def _resolve_user_id(
    conn,
    current_user,
    user_telegram_id: Optional[int] = None,
    create_from_telegram: bool = True,
) -> Optional[int]:
    """
    Возвращает user_id в приоритете:
    1) из AuthUser (OAuth/JWT)
    2) из user_telegram_id (legacy flow)
    """
    auth_user_id = getattr(current_user, "user_id", None)
    if auth_user_id:
        return int(auth_user_id)
    if user_telegram_id is None:
        return None
    return get_or_create_user(conn, int(user_telegram_id), None) if create_from_telegram else None


def _parse_user_telegram_id(value: Optional[str]) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _get_user_telegram_id(conn, user_id: Optional[int]) -> Optional[int]:
    """Возвращает Telegram ID пользователя по user_id."""
    if not user_id:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT telegram_id FROM users WHERE id = %s LIMIT 1", (user_id,))
            row = cur.fetchone()
        if not row:
            return None
        tg_id = row.get("telegram_id")
        return int(tg_id) if tg_id is not None else None
    except Exception as e:
        logger.warning("Не удалось получить telegram_id для user_id=%s: %s", user_id, e)
        return None


def _load_user_telegram_credentials(conn, user_id: Optional[int]) -> Optional[dict]:
    """Читает персональные Telethon credentials пользователя из БД."""
    if not user_id:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT tg_api_id, tg_api_hash, tg_phone, tg_session_file
                FROM user_telegram_credentials
                WHERE user_id = %s AND is_active = true
                LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        session_file = row.get("tg_session_file") or _default_telethon_session_file(int(user_id))
        return {
            "tg_api_id": int(row["tg_api_id"]),
            "tg_api_hash": (row.get("tg_api_hash") or "").strip(),
            "tg_phone": row.get("tg_phone"),
            "tg_session_file": session_file,
        }
    except Exception as e:
        logger.warning("Не удалось загрузить user_telegram_credentials для user_id=%s: %s", user_id, e)
        return None


def _get_user_default_bot(conn, user_id: Optional[int]) -> Optional[dict]:
    """Возвращает дефолтный активный бот пользователя."""
    if not user_id:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, bot_name, bot_token, is_default, is_active, updated_at
                FROM user_bot_credentials
                WHERE user_id = %s AND is_active = true
                ORDER BY is_default DESC, updated_at DESC, id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            return cur.fetchone()
    except Exception as e:
        logger.warning("Не удалось загрузить user_bot_credentials для user_id=%s: %s", user_id, e)
        return None


def _mask_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    s = token.strip()
    if len(s) <= 8:
        return "*" * len(s)
    return s[:4] + "*" * (len(s) - 8) + s[-4:]


def _write_user_secret_file(conn, user_id: int) -> Optional[str]:
    """
    Генерирует per-user env файл с Telegram runtime credentials.
    Файл нужен воркерам/инструментам при запуске от имени пользователя.
    """
    creds = _load_user_telegram_credentials(conn, user_id)
    if not creds:
        return None

    bot = _get_user_default_bot(conn, user_id)
    USER_SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    secret_file = USER_SECRETS_DIR / f"user_{user_id}.env"

    lines = [
        f"USER_ID={user_id}",
        f"TG_API_ID={creds['tg_api_id']}",
        f"TG_API_HASH={creds['tg_api_hash']}",
        f"TG_SESSION_FILE={creds['tg_session_file']}",
    ]
    if creds.get("tg_phone"):
        lines.append(f"TG_PHONE={creds['tg_phone']}")
    if bot and bot.get("bot_token"):
        lines.append(f"TG_BOT_TOKEN={bot['bot_token']}")
        lines.append(f"TG_BOT_NAME={bot.get('bot_name') or 'Default Bot'}")

    content = "\n".join(lines) + "\n"
    secret_file.write_text(content, encoding="utf-8")
    os.chmod(secret_file, 0o600)
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_secret_files (user_id, secret_file_path, file_checksum, generated_at, updated_at)
            VALUES (%s, %s, %s, now(), now())
            ON CONFLICT (user_id) DO UPDATE SET
                secret_file_path = EXCLUDED.secret_file_path,
                file_checksum = EXCLUDED.file_checksum,
                generated_at = now(),
                updated_at = now()
            """,
            (user_id, str(secret_file), checksum),
        )
    return str(secret_file)


def _get_pending_telethon_auth(user_id: int) -> Optional[TelethonPendingAuth]:
    pending = _TELETHON_PENDING_AUTH.get(user_id)
    if not pending:
        return None
    if datetime.now() - pending.created_at > timedelta(seconds=TELETHON_CODE_TTL_SECONDS):
        _TELETHON_PENDING_AUTH.pop(user_id, None)
        return None
    return pending


def _clear_pending_telethon_auth(user_id: int) -> None:
    _TELETHON_PENDING_AUTH.pop(user_id, None)


def _remove_session_artifacts(session_file: str) -> None:
    if not session_file:
        return
    base = Path(session_file)
    for path in (
        base,
        Path(str(base) + "-journal"),
        Path(str(base) + ".journal"),
    ):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            logger.warning("Не удалось удалить session artifact: %s", path)


def _sync_user_telegram_identity(conn, user_id: int, telegram_id: int) -> None:
    """Привязывает telegram_id к users.id либо проверяет, что он совпадает."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT telegram_id FROM users WHERE id = %s LIMIT 1", (user_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        current_tg_id = row.get("telegram_id")
        if current_tg_id is None:
            cur.execute(
                "SELECT id FROM users WHERE telegram_id = %s AND id <> %s LIMIT 1",
                (telegram_id, user_id),
            )
            existing = cur.fetchone()
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Этот Telegram аккаунт уже привязан к другому пользователю. "
                        "Используйте login того же владельца Telegram или отвяжите аккаунт у администратора."
                    ),
                )
            cur.execute("UPDATE users SET telegram_id = %s, updated_at = now() WHERE id = %s", (telegram_id, user_id))
            return
        if int(current_tg_id) != int(telegram_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Аккаунт Telegram не совпадает с профилем пользователя. "
                    f"Ожидается Telegram ID {current_tg_id}, получен {telegram_id}."
                ),
            )


async def check_chat_access(
    chat_id: int,
    *,
    tg_api_id: Optional[int] = None,
    tg_api_hash: Optional[str] = None,
    tg_session_file: Optional[str] = None,
) -> tuple[bool, Optional[str], Optional[str], Optional[str]]:
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
        
        api_id = int(tg_api_id or int((os.environ.get("TG_API_ID", "") or "0").strip() or 0))
        api_hash = (tg_api_hash or os.environ.get("TG_API_HASH", "") or "").strip()
        session_file = (tg_session_file or os.environ.get("TG_SESSION_FILE", "") or "").strip()
        
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


async def check_recipient_access(
    recipient_id: int,
    *,
    tg_api_id: Optional[int] = None,
    tg_api_hash: Optional[str] = None,
    tg_session_file: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
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
        
        api_id = int(tg_api_id or int((os.environ.get("TG_API_ID", "") or "0").strip() or 0))
        api_hash = (tg_api_hash or os.environ.get("TG_API_HASH", "") or "").strip()
        session_file = (tg_session_file or os.environ.get("TG_SESSION_FILE", "") or "").strip()
        
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
            # Для получателей делаем проверку более мягкой - если ID выглядит валидным, разрешаем
            # Это может быть бот или пользователь, с которым система еще не взаимодействовала
            if recipient_id > 0:  # Валидный положительный ID пользователя/бота
                logger.info(f"Получатель {recipient_id} не найден в контактах, но ID выглядит валидным - разрешаем использование")
                return True, None
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
            error_msg = str(e)
            await client.disconnect()
            # Если ошибка связана с тем, что сущность не найдена (бот не в контактах), разрешаем использование
            if "Could not find the input entity" in error_msg or "not found" in error_msg.lower():
                if recipient_id > 0:  # Валидный положительный ID
                    logger.info(f"Получатель {recipient_id} не найден в контактах Telethon, но ID валидный - разрешаем использование")
                    return True, None
            logger.warning(f"Проверка получателя {recipient_id}: {e}")
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
    db=Depends(get_db),
):
    """Главная страница с формой добавления чата"""
    user_id = _resolve_user_id(db, current_user, create_from_telegram=False)
    user_telegram_id = _get_user_telegram_id(db, user_id)
    return templates.TemplateResponse("index.html", {"request": request, "user_telegram_id": user_telegram_id or ""})


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(
    request: Request,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Персональная страница первичной настройки Telegram/Telethon для пользователя."""
    user_id = _resolve_user_id(db, current_user, create_from_telegram=False)
    user_telegram_id = _get_user_telegram_id(db, user_id)
    is_admin = _is_admin_user(current_user)
    return templates.TemplateResponse("setup.html", {
        "request": request,
        "user_id": user_id or "",
        "user_telegram_id": user_telegram_id or "",
        "is_admin": is_admin,
        "admin_login": AUTH_LOCAL_ADMIN_LOGIN,
    })


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next_url: Optional[str] = None, error_msg: Optional[str] = None):
    """Страница входа: OAuth (Google/Яндекс), auth-сервис (логин/пароль) или Telegram ID"""
    next_url = _normalize_next_path(next_url or request.query_params.get("next", "/"))
    err = error_msg or request.query_params.get("error")
    if AUTH_LOCAL_ENABLED:
        yandex_enabled = bool(AUTH_OWN_ENABLED and YANDEX_CLIENT_ID)
        base_url = (os.environ.get("BASE_URL", "") or "").strip().rstrip("/")
        return templates.TemplateResponse("login_local.html", {
            "request": request,
            "next_url": next_url,
            "next_encoded": quote(next_url, safe=""),
            "error_msg": err,
            "oauth_enabled": bool(AUTH_OWN_ENABLED),
            "yandex_enabled": yandex_enabled,
            "yandex_redirect_uri": f"{base_url}/auth/yandex/callback" if base_url else "",
        })
    if AUTH_OWN_ENABLED:
        yandex_enabled = bool(YANDEX_CLIENT_ID)
        return templates.TemplateResponse("login_oauth.html", {
            "request": request,
            "next_url": next_url,
            "next_encoded": quote(next_url, safe=""),
            "error_msg": err,
            "yandex_enabled": yandex_enabled,
            "yandex_redirect_uri": _oauth_callback_uri(request),
        })
    if AUTH_CHECK_ENABLED:
        return templates.TemplateResponse("login_auth.html", {
            "request": request,
            "next_url": next_url,
            "error_msg": err,
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
    """Вход через local auth или внешний auth-сервис: установка cookie, редирект."""
    next_path = _normalize_next_path(next_path)

    if AUTH_LOCAL_ENABLED:
        login = _normalize_login(username)
        if not login or not password:
            return RedirectResponse(
                url=f"/login?next={quote(next_path, safe='')}&error={quote('Введите логин и пароль', safe='')}",
                status_code=302,
            )
        try:
            with db.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT ula.user_id, ula.password_hash, ula.is_active
                    FROM user_local_auth ula
                    JOIN users u ON u.id = ula.user_id
                    WHERE ula.login = %s
                    LIMIT 1
                    """,
                    (login,),
                )
                row = cur.fetchone()
            if not row or not row.get("is_active") or not _verify_password(password, row.get("password_hash", "")):
                return RedirectResponse(
                    url=f"/login?next={quote(next_path, safe='')}&error={quote('Неверный логин или пароль', safe='')}",
                    status_code=302,
                )
            user_id = int(row["user_id"])
            session_token = _create_local_session(db, user_id)
            db.commit()
            audit_log(db, user_id, "login_local", {"login": login}, request)
            response = RedirectResponse(url=_post_login_redirect(next_path), status_code=302)
            _set_local_session_cookie(response, session_token)
            response.delete_cookie(AUTH_COOKIE_NAME, path="/")
            return response
        except Exception as e:
            logger.error("Ошибка local login: %s", e)
            db.rollback()
            return RedirectResponse(
                url=f"/login?next={quote(next_path, safe='')}&error={quote('Ошибка входа. Попробуйте позже', safe='')}",
                status_code=302,
            )

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


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, next_url: Optional[str] = None, error_msg: Optional[str] = None):
    """Страница регистрации в local auth режиме."""
    if not AUTH_LOCAL_ENABLED:
        return RedirectResponse(url="/login", status_code=302)
    next_url = _normalize_next_path(next_url or request.query_params.get("next", "/"))
    return templates.TemplateResponse("register_local.html", {
        "request": request,
        "next_url": next_url,
        "error_msg": error_msg or request.query_params.get("error"),
    })


@app.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    login: str = Form(default=""),
    password: str = Form(default=""),
    password_confirm: str = Form(default=""),
    telegram_id: str = Form(default=""),
    display_name: str = Form(default=""),
    next_path: str = Form(default="/", alias="next"),
    db=Depends(get_db),
):
    """Регистрация local пользователя: users + user_local_auth + сессия."""
    if not AUTH_LOCAL_ENABLED:
        return RedirectResponse(url="/login", status_code=302)

    next_path = _normalize_next_path(next_path)
    norm_login = _normalize_login(login)

    if not _is_valid_login(norm_login):
        return RedirectResponse(
            url=f"/register?next={quote(next_path, safe='')}&error={quote('Логин: 3-64 символа [a-z0-9_.-]', safe='')}",
            status_code=302,
        )
    if not _is_valid_password(password):
        return RedirectResponse(
            url=f"/register?next={quote(next_path, safe='')}&error={quote(f'Пароль должен быть не короче {AUTH_LOCAL_MIN_PASSWORD_LEN} символов', safe='')}",
            status_code=302,
        )
    if password != password_confirm:
        return RedirectResponse(
            url=f"/register?next={quote(next_path, safe='')}&error={quote('Пароли не совпадают', safe='')}",
            status_code=302,
        )

    tg_id_value: Optional[int] = None
    tg_id_raw = (telegram_id or "").strip()
    if tg_id_raw:
        try:
            tg_id_value = int(tg_id_raw)
            if tg_id_value == 0:
                raise ValueError("zero")
        except ValueError:
            return RedirectResponse(
                url=f"/register?next={quote(next_path, safe='')}&error={quote('Telegram ID должен быть числом и не равен 0', safe='')}",
                status_code=302,
            )

    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT user_id FROM user_local_auth WHERE login = %s LIMIT 1", (norm_login,))
            if cur.fetchone():
                return RedirectResponse(
                    url=f"/register?next={quote(next_path, safe='')}&error={quote('Такой логин уже занят', safe='')}",
                    status_code=302,
                )

            existing = None
            if tg_id_value is not None:
                cur.execute("SELECT id, name FROM users WHERE telegram_id = %s LIMIT 1", (tg_id_value,))
                existing = cur.fetchone()

            if existing:
                user_id = int(existing["id"])
                user_name = (display_name or "").strip()
                if user_name:
                    cur.execute(
                        "UPDATE users SET name = %s, updated_at = now() WHERE id = %s",
                        (user_name, user_id),
                    )
            else:
                if tg_id_value is not None:
                    user_name = (display_name or "").strip() or f"User {tg_id_value}"
                else:
                    user_name = (display_name or "").strip() or norm_login
                cur.execute(
                    """
                    INSERT INTO users (telegram_id, name, is_active)
                    VALUES (%s, %s, true)
                    RETURNING id
                    """,
                    (tg_id_value, user_name),
                )
                user_id = int(cur.fetchone()["id"])

            cur.execute("SELECT id FROM user_local_auth WHERE user_id = %s LIMIT 1", (user_id,))
            if cur.fetchone():
                return RedirectResponse(
                    url=f"/register?next={quote(next_path, safe='')}&error={quote('Для этого Telegram ID уже создан login/password', safe='')}",
                    status_code=302,
                )

            cur.execute(
                """
                INSERT INTO user_local_auth (user_id, login, password_hash, is_active)
                VALUES (%s, %s, %s, true)
                """,
                (user_id, norm_login, _hash_password(password)),
            )
            session_token = _create_local_session(db, user_id)

        db.commit()
        audit_log(db, user_id, "register_local", {"login": norm_login, "telegram_id": tg_id_value}, request)
        response = RedirectResponse(url=_post_login_redirect(next_path), status_code=302)
        _set_local_session_cookie(response, session_token)
        response.delete_cookie(AUTH_COOKIE_NAME, path="/")
        return response
    except Exception as e:
        logger.error("Ошибка local register: %s", e)
        db.rollback()
        return RedirectResponse(
            url=f"/register?next={quote(next_path, safe='')}&error={quote('Ошибка регистрации. Попробуйте позже', safe='')}",
            status_code=302,
        )


# OAuth: редирект на провайдера (только Яндекс)
@app.get("/auth/yandex")
async def auth_yandex_redirect(request: Request):
    """Редирект на Yandex OAuth."""
    if not AUTH_OWN_ENABLED or not get_yandex_authorize_url:
        raise HTTPException(status_code=404, detail="OAuth не настроен")
    next_path = request.query_params.get("next", "/")
    state = secrets.token_urlsafe(32)
    redirect_uri = _oauth_callback_uri(request)
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
    redirect_uri = _oauth_callback_uri(request)
    result = await exchange_yandex_code(code, redirect_uri)
    if not result:
        return RedirectResponse(url=f"/login?error={quote('Не удалось получить данные от Yandex', safe='')}", status_code=302)
    external_id, email, display_name = result
    user_id = get_or_create_user_by_oauth(db, "yandex", external_id, email, display_name)
    token = create_access_token(user_id, email, display_name)
    audit_log(db, user_id, "login", {"provider": "yandex", "email": email}, request)
    response = RedirectResponse(url=next_path if next_path.startswith("/") else "/", status_code=302)
    response.set_cookie(AUTH_COOKIE_NAME, token, max_age=3600, path="/", httponly=True, samesite="lax")
    # Если пользователь ранее заходил по local auth, очищаем local-cookie,
    # чтобы приоритетно использовалась текущая OAuth-сессия.
    response.delete_cookie(AUTH_LOCAL_COOKIE_NAME, path="/")
    response.delete_cookie("oauth_state", path="/")
    response.delete_cookie("oauth_next", path="/")
    return response


@app.get("/logout")
async def logout_page(request: Request, db=Depends(get_db)):
    """Сброс cookie авторизации и редирект на главную. Аудит выхода — по cookie до удаления."""
    user_id = None
    logout_method = None

    local_session_token = request.cookies.get(AUTH_LOCAL_COOKIE_NAME)
    if AUTH_LOCAL_ENABLED and local_session_token:
        local_user = _get_local_user_by_session(local_session_token)
        if local_user:
            user_id = local_user.user_id
            logout_method = "local"
        try:
            _delete_local_session(db, local_session_token)
            db.commit()
        except Exception:
            db.rollback()

    token = request.cookies.get(AUTH_COOKIE_NAME)
    if AUTH_OWN_ENABLED and verify_access_token and token:
        au = verify_access_token(token)
        if au:
            user_id = au.user_id
            logout_method = "oauth"
    if user_id is not None:
        audit_log(db, user_id, "logout", {"method": logout_method}, request)
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(AUTH_LOCAL_COOKIE_NAME, path="/")
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return response


@app.get("/channels", response_class=HTMLResponse)
async def channels_page(
    request: Request,
    current_user: Optional[str] = Depends(get_current_auth_user),
    user_telegram_id: Optional[int] = None,
    db=Depends(get_db),
):
    """Страница со списком каналов пользователя"""
    if user_telegram_id is None:
        resolved_user_id = _resolve_user_id(db, current_user, create_from_telegram=False)
        if resolved_user_id:
            user_telegram_id = _get_user_telegram_id(db, resolved_user_id)
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


@app.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Страница управления пользователями: список пользователей и аудит входа/выхода."""
    data = list_users_with_identities(db, audit_limit=100)
    return templates.TemplateResponse("users.html", {
        "request": request,
        "users": data["users"],
        "audit": data["audit"],
    })


@app.get("/api/admin/users", response_class=JSONResponse)
async def api_admin_users(
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Список пользователей с привязками OAuth и последние записи аудита. Доступ только после идентификации."""
    if AUTH_REQUIRED and current_user is None:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    return list_users_with_identities(db, audit_limit=100)


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
    db=Depends(get_db),
):
    """Библиотека промптов (по умолчанию) или редактор канала (если передан channel_id)."""
    channel_id = request.query_params.get("channel_id")
    remote_user = request.headers.get("X-Remote-User", "")
    resolved_user_id = _resolve_user_id(db, current_user, create_from_telegram=False)
    user_telegram_id = _get_user_telegram_id(db, resolved_user_id)
    context = {"request": request, "remote_user": remote_user, "user_telegram_id": user_telegram_id or ""}
    if channel_id:
        return templates.TemplateResponse("prompts_v2.html", context)
    return templates.TemplateResponse("prompts_library.html", context)


@app.get("/api/check-chat", response_class=JSONResponse)
async def api_check_chat(
    request: Request,
    chat_id: str = Query(..., description="ID чата для проверки доступа"),
    user_telegram_id: Optional[int] = Query(None, description="Telegram ID пользователя (legacy)"),
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """
    Проверяет наличие и доступность чата/канала для системы (по факту ввода).
    Доступ только после идентификации.
    """
    if AUTH_REQUIRED and current_user is None:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
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
    
    # Если ID положительный и больше 0, пробуем также с отрицательным (для групп/супергрупп)
    # Telegram API использует отрицательные ID для групп: -1000000000000 - group_id
    # Но также может быть просто отрицательный ID типа -5228538198
    has_access = False
    peer_type = None
    name = None
    err = None
    
    resolved_user_id = _resolve_user_id(db, current_user, user_telegram_id=user_telegram_id)
    creds = _load_user_telegram_credentials(db, resolved_user_id)

    # Сначала пробуем с исходным ID
    has_access, peer_type, name, err = await check_chat_access(
        cid,
        tg_api_id=(creds or {}).get("tg_api_id"),
        tg_api_hash=(creds or {}).get("tg_api_hash"),
        tg_session_file=(creds or {}).get("tg_session_file"),
    )
    
    # Если не получилось и ID положительный, пробуем с отрицательным
    if not has_access and cid > 0:
        negative_id = -cid
        logger.info(f"Пробуем отрицательный ID для группы: {negative_id}")
        has_access, peer_type, name, err = await check_chat_access(
            negative_id,
            tg_api_id=(creds or {}).get("tg_api_id"),
            tg_api_hash=(creds or {}).get("tg_api_hash"),
            tg_session_file=(creds or {}).get("tg_session_file"),
        )
        if has_access:
            # Обновляем сообщение, чтобы указать правильный ID
            err = None
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
async def api_check_recipient(
    recipient_id: str = Query(..., description="ID получателя дайджестов"),
    user_telegram_id: Optional[int] = Query(None, description="Telegram ID пользователя (legacy)"),
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """
    Проверяет доступность получателя для системы (по факту ввода).
    Доступ только после идентификации.
    """
    if AUTH_REQUIRED and current_user is None:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
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
    resolved_user_id = _resolve_user_id(db, current_user, user_telegram_id=user_telegram_id)
    creds = _load_user_telegram_credentials(db, resolved_user_id)
    ok, err = await check_recipient_access(
        rid,
        tg_api_id=(creds or {}).get("tg_api_id"),
        tg_api_hash=(creds or {}).get("tg_api_hash"),
        tg_session_file=(creds or {}).get("tg_session_file"),
    )
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


@app.get("/api/user/runtime-config", response_class=JSONResponse)
async def get_user_runtime_config(
    user_telegram_id: Optional[int] = None,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Возвращает персональные Telegram runtime-настройки пользователя (без утечки секретов)."""
    try:
        user_id = _resolve_user_id(db, current_user, user_telegram_id=user_telegram_id, create_from_telegram=True)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")
        is_admin = _is_admin_user(current_user)

        creds = _load_user_telegram_credentials(db, user_id)
        bot = _get_user_default_bot(db, user_id)

        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT secret_file_path, file_checksum, generated_at
                FROM user_secret_files
                WHERE user_id = %s
                LIMIT 1
                """,
                (user_id,),
            )
            secret_row = cur.fetchone()
        session_exists = False
        session_path = (creds or {}).get("tg_session_file")
        if session_path:
            try:
                session_exists = Path(session_path).exists()
            except Exception:
                session_exists = False

        return {
            "user_id": user_id,
            "is_admin": is_admin,
            "telegram_id": _get_user_telegram_id(db, user_id),
            "telegram": {
                "configured": bool(creds),
                "tg_api_id": creds.get("tg_api_id") if creds else None,
                "tg_phone": creds.get("tg_phone") if creds else None,
                "tg_session_file": creds.get("tg_session_file") if creds and is_admin else None,
                "session_path_controlled_by_admin": not is_admin,
                "session_file_exists": session_exists,
            },
            "bot": {
                "configured": bool(bot),
                "id": bot.get("id") if bot else None,
                "bot_name": bot.get("bot_name") if bot else None,
                "bot_token_masked": _mask_token(bot.get("bot_token")) if bot else None,
                "is_default": bool(bot.get("is_default")) if bot else False,
            },
            "secret_file": {
                "path": secret_row.get("secret_file_path") if secret_row and is_admin else None,
                "generated": bool(secret_row),
                "checksum": secret_row.get("file_checksum") if secret_row else None,
                "generated_at": secret_row.get("generated_at").isoformat() if secret_row and secret_row.get("generated_at") else None,
            },
        }
    except HTTPException:
        raise
    except psycopg2.ProgrammingError as e:
        if "does not exist" in str(e).lower():
            raise HTTPException(status_code=400, detail="Выполните миграцию 009_user_runtime_and_prompt_sharing.sql.")
        raise
    except Exception as e:
        logger.error("Ошибка получения runtime-config пользователя: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/user/runtime-config", response_class=JSONResponse)
async def set_user_runtime_config(
    request: Request,
    body: UserRuntimeConfigUpdate,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """
    Сохраняет персональные Telegram credentials и бота пользователя в БД.
    Дополнительно генерирует / обновляет per-user secret env файл.
    """
    try:
        user_id = _resolve_user_id(db, current_user, user_telegram_id=body.user_telegram_id)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")
        is_admin = _is_admin_user(current_user)
        if not body.tg_api_id or not body.tg_api_hash.strip():
            raise HTTPException(status_code=400, detail="tg_api_id и tg_api_hash обязательны")

        default_session_file = _default_telethon_session_file(int(user_id))
        if is_admin:
            session_file = (body.tg_session_file or default_session_file).strip()
        else:
            # Для рядового пользователя путь хранится в едином системном каталоге и не редактируется.
            session_file = default_session_file
        tg_api_hash = body.tg_api_hash.strip()

        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_telegram_credentials (
                    user_id, tg_api_id, tg_api_hash, tg_phone, tg_session_file, is_active
                ) VALUES (%s, %s, %s, %s, %s, true)
                ON CONFLICT (user_id) DO UPDATE SET
                    tg_api_id = EXCLUDED.tg_api_id,
                    tg_api_hash = EXCLUDED.tg_api_hash,
                    tg_phone = EXCLUDED.tg_phone,
                    tg_session_file = EXCLUDED.tg_session_file,
                    is_active = true,
                    updated_at = now()
                """,
                (user_id, int(body.tg_api_id), tg_api_hash, body.tg_phone, session_file),
            )

            created_bot_id = None
            if body.bot_token and body.bot_token.strip():
                bot_token = body.bot_token.strip()
                if body.make_bot_default:
                    cur.execute(
                        "UPDATE user_bot_credentials SET is_default = false WHERE user_id = %s",
                        (user_id,),
                    )
                cur.execute(
                    """
                    INSERT INTO user_bot_credentials (user_id, bot_name, bot_token, is_active, is_default)
                    VALUES (%s, %s, %s, true, %s)
                    RETURNING id
                    """,
                    (user_id, (body.bot_name or "Default Bot").strip(), bot_token, body.make_bot_default),
                )
                created_bot_id = cur.fetchone()[0]

            secret_file_path = _write_user_secret_file(db, user_id)
            db.commit()

        audit_log(
            db, _audit_user_id(current_user), "user_runtime_config_updated",
            {"user_id": user_id, "created_bot_id": created_bot_id}, request,
            "user_runtime", str(user_id),
        )
        return {
            "success": True,
            "user_id": user_id,
            "secret_file_path": secret_file_path if is_admin else None,
            "secret_file_generated": bool(secret_file_path),
            "created_bot_id": created_bot_id,
            "message": "Персональные runtime-настройки сохранены",
        }
    except HTTPException:
        raise
    except psycopg2.ProgrammingError as e:
        db.rollback()
        if "does not exist" in str(e).lower():
            raise HTTPException(status_code=400, detail="Выполните миграцию 009_user_runtime_and_prompt_sharing.sql.")
        raise
    except Exception as e:
        logger.error("Ошибка сохранения runtime-config пользователя: %s", e)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/user/telethon/send-code", response_class=JSONResponse)
async def send_telethon_code(
    request: Request,
    body: TelethonSendCodeRequest,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Отправляет код подтверждения Telegram для web-авторизации Telethon."""
    client = None
    try:
        from telethon import TelegramClient
        from telethon.errors import ApiIdInvalidError, FloodWaitError, PhoneNumberInvalidError
    except Exception as e:
        logger.error("Telethon import error: %s", e)
        raise HTTPException(status_code=500, detail="Telethon недоступен на сервере")

    try:
        user_id = _resolve_user_id(db, current_user, user_telegram_id=body.user_telegram_id)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")

        creds = _load_user_telegram_credentials(db, user_id)
        if not creds:
            raise HTTPException(status_code=400, detail="Сначала сохраните API ID/API HASH на странице настройки")

        phone = (body.tg_phone or creds.get("tg_phone") or "").strip()
        if not phone:
            raise HTTPException(status_code=400, detail="Укажите телефон Telegram в формате +7999...")
        if not re.fullmatch(r"\+\d{6,20}", phone):
            raise HTTPException(status_code=400, detail="Телефон должен быть в формате +79991234567")

        # Если телефон в форме отличается от сохраненного — обновим его в БД.
        if phone != (creds.get("tg_phone") or ""):
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE user_telegram_credentials SET tg_phone = %s, updated_at = now() WHERE user_id = %s",
                    (phone, user_id),
                )

        session_file = (creds.get("tg_session_file") or "").strip()
        if not session_file:
            raise HTTPException(status_code=400, detail="Не задан tg_session_file. Сначала сохраните runtime-настройки.")
        Path(session_file).parent.mkdir(parents=True, exist_ok=True)

        client = TelegramClient(session_file, int(creds["tg_api_id"]), creds["tg_api_hash"])
        await client.connect()

        if await client.is_user_authorized():
            me = await client.get_me()
            if not me or not getattr(me, "id", None):
                raise HTTPException(status_code=500, detail="Не удалось определить аккаунт Telegram после авторизации")
            try:
                _sync_user_telegram_identity(db, user_id, int(me.id))
            except HTTPException:
                _remove_session_artifacts(session_file)
                raise
            secret_file_path = _write_user_secret_file(db, user_id)
            db.commit()
            _clear_pending_telethon_auth(user_id)
            audit_log(
                db, _audit_user_id(current_user), "telethon_auth_already_authorized",
                {"telegram_id": int(me.id)}, request, "user_runtime", str(user_id),
            )
            return {
                "success": True,
                "already_authorized": True,
                "telegram_id": int(me.id),
                "phone": phone,
                "session_file": session_file if is_admin else None,
                "secret_file_path": secret_file_path if is_admin else None,
                "secret_file_generated": bool(secret_file_path),
                "message": "Telethon уже авторизован для этого пользователя.",
            }

        sent = await client.send_code_request(phone)
        code_hash = (getattr(sent, "phone_code_hash", None) or "").strip()
        if not code_hash:
            raise HTTPException(status_code=500, detail="Не удалось получить phone_code_hash от Telegram")

        _TELETHON_PENDING_AUTH[user_id] = TelethonPendingAuth(
            phone=phone,
            phone_code_hash=code_hash,
            tg_api_id=int(creds["tg_api_id"]),
            tg_api_hash=creds["tg_api_hash"],
            tg_session_file=session_file,
            created_at=datetime.now(),
        )
        db.commit()
        audit_log(
            db, _audit_user_id(current_user), "telethon_code_sent",
            {"phone": phone}, request, "user_runtime", str(user_id),
        )
        return {
            "success": True,
            "already_authorized": False,
            "phone": phone,
            "ttl_seconds": TELETHON_CODE_TTL_SECONDS,
            "message": "Код отправлен в Telegram. Введите его в поле подтверждения.",
        }
    except HTTPException:
        db.rollback()
        raise
    except ApiIdInvalidError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Некорректные API ID / API HASH")
    except PhoneNumberInvalidError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Некорректный номер телефона Telegram")
    except FloodWaitError as e:
        db.rollback()
        wait_sec = int(getattr(e, "seconds", 0) or 0)
        raise HTTPException(status_code=429, detail=f"Слишком частые запросы. Повторите через {wait_sec} сек.")
    except Exception as e:
        logger.exception("Ошибка отправки кода Telethon: %s", e)
        db.rollback()
        raise HTTPException(status_code=500, detail="Не удалось отправить код подтверждения")
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass


@app.post("/api/user/telethon/verify-code", response_class=JSONResponse)
async def verify_telethon_code(
    request: Request,
    body: TelethonVerifyCodeRequest,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Подтверждает код Telegram (и 2FA при необходимости), завершая авторизацию Telethon."""
    client = None
    try:
        from telethon import TelegramClient
        from telethon.errors import (
            SessionPasswordNeededError,
            PhoneCodeInvalidError,
            PhoneCodeExpiredError,
            PasswordHashInvalidError,
        )
    except Exception as e:
        logger.error("Telethon import error: %s", e)
        raise HTTPException(status_code=500, detail="Telethon недоступен на сервере")

    try:
        user_id = _resolve_user_id(db, current_user, user_telegram_id=body.user_telegram_id)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")
        is_admin = _is_admin_user(current_user)

        pending = _get_pending_telethon_auth(user_id)
        if not pending:
            raise HTTPException(status_code=400, detail="Срок действия кода истёк. Нажмите «Отправить код» снова.")

        code = re.sub(r"\s+", "", (body.code or ""))
        if not code:
            raise HTTPException(status_code=400, detail="Введите код подтверждения из Telegram")

        Path(pending.tg_session_file).parent.mkdir(parents=True, exist_ok=True)
        client = TelegramClient(pending.tg_session_file, pending.tg_api_id, pending.tg_api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            try:
                await client.sign_in(
                    phone=pending.phone,
                    code=code,
                    phone_code_hash=pending.phone_code_hash,
                )
            except SessionPasswordNeededError:
                password = (body.password or "").strip()
                if not password:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "success": False,
                            "needs_password": True,
                            "message": "Для этого аккаунта включен пароль 2FA. Введите пароль Telegram и повторите.",
                        },
                    )
                await client.sign_in(password=password)

        me = await client.get_me()
        if not me or not getattr(me, "id", None):
            raise HTTPException(status_code=500, detail="Не удалось получить данные профиля Telegram")

        try:
            _sync_user_telegram_identity(db, user_id, int(me.id))
        except HTTPException:
            _remove_session_artifacts(pending.tg_session_file)
            _clear_pending_telethon_auth(user_id)
            db.rollback()
            raise

        secret_file_path = _write_user_secret_file(db, user_id)
        db.commit()
        _clear_pending_telethon_auth(user_id)
        audit_log(
            db, _audit_user_id(current_user), "telethon_auth_completed",
            {"telegram_id": int(me.id), "phone": pending.phone}, request, "user_runtime", str(user_id),
        )
        return {
            "success": True,
            "telegram_id": int(me.id),
            "telegram_name": getattr(me, "first_name", None) or getattr(me, "username", None),
            "phone": pending.phone,
            "session_file": pending.tg_session_file if is_admin else None,
            "secret_file_path": secret_file_path if is_admin else None,
            "secret_file_generated": bool(secret_file_path),
            "message": "Telethon успешно авторизован.",
        }
    except HTTPException:
        db.rollback()
        raise
    except PhoneCodeInvalidError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Неверный код подтверждения")
    except PhoneCodeExpiredError:
        if "user_id" in locals():
            _clear_pending_telethon_auth(user_id)
        db.rollback()
        raise HTTPException(status_code=400, detail="Код подтверждения истёк. Запросите новый код.")
    except PasswordHashInvalidError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Неверный пароль 2FA Telegram")
    except Exception as e:
        logger.exception("Ошибка подтверждения кода Telethon: %s", e)
        db.rollback()
        raise HTTPException(status_code=500, detail="Не удалось завершить авторизацию Telethon")
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass


@app.get("/api/channels", response_class=JSONResponse)
async def list_channels(
    user_telegram_id: Optional[str] = None,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Возвращает список каналов пользователя"""
    try:
        user_telegram_id_i = _parse_user_telegram_id(user_telegram_id)
        user_id = _resolve_user_id(db, current_user, user_telegram_id=user_telegram_id_i)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")
        
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
                        "delivery_importance": ch.get('delivery_importance') or "important",
                        "delivery_send_file": ch.get('delivery_send_file', True),
                        "delivery_send_text": ch.get('delivery_send_text', True),
                        "delivery_text_max_chars": ch.get('delivery_text_max_chars'),
                        "delivery_summary_only": ch.get('delivery_summary_only', False),
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
    require_user_telegram_id: bool = True,
) -> List[dict]:
    """Проверка параметров добавления канала. Возвращает список ошибок [{field, message}]."""
    errors = []
    if require_user_telegram_id:
        if not user_telegram_id or not str(user_telegram_id).strip():
            errors.append({"field": "user_telegram_id", "message": "Укажите ваш Telegram ID (число). Узнать можно через @userinfobot."})
        else:
            try:
                uid = int(user_telegram_id)
                if uid == 0:
                    errors.append({"field": "user_telegram_id", "message": "Telegram ID не может быть нулём. Укажите ваш реальный ID."})
            except (TypeError, ValueError):
                errors.append({"field": "user_telegram_id", "message": "Telegram ID должен быть числом (например: 499412926)."})
    elif user_telegram_id and str(user_telegram_id).strip():
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
    user_telegram_id: Optional[str] = Form(None, description="Telegram ID пользователя"),
    telegram_chat_id: str = Form(..., description="ID чата для мониторинга"),
    name: Optional[str] = Form(None),
    recipient_telegram_id: str = Form(..., description="ID получателя дайджестов"),
    recipient_name: Optional[str] = Form(None),
    prompt_file: str = Form("prompts/digest_management.md"),
    poll_interval_minutes: int = Form(60),
    delivery_importance: str = Form("important"),
    delivery_send_file: str = Form("true"),
    delivery_send_text: str = Form("true"),
    delivery_text_max_chars: Optional[str] = Form(None),
    delivery_summary_only: str = Form("false"),
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Добавляет новый канал для пользователя. Перед добавлением проверяет все параметры и доступность чата."""
    auth_user_id = getattr(current_user, "user_id", None)

    # 1. Проверка формата и обязательности полей
    validation_errors = _validate_channel_params(
        user_telegram_id,
        telegram_chat_id,
        recipient_telegram_id,
        require_user_telegram_id=not bool(auth_user_id),
    )
    if validation_errors:
        return JSONResponse(
            status_code=400,
            content={"success": False, "errors": validation_errors, "message": "Исправьте указанные поля и отправьте форму снова."}
        )

    uid = _parse_user_telegram_id(user_telegram_id)
    chat_id = int(telegram_chat_id)
    recip_id = int(recipient_telegram_id)

    try:
        # 2. Получаем или создаём пользователя
        user_id = _resolve_user_id(db, current_user, user_telegram_id=uid)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")

        # 3. Проверяем наличие и доступ к чату для мониторинга (персональной сессией пользователя, если настроена)
        user_creds = _load_user_telegram_credentials(db, user_id)
        has_access, peer_type, chat_name, chat_err = await check_chat_access(
            chat_id,
            tg_api_id=(user_creds or {}).get("tg_api_id"),
            tg_api_hash=(user_creds or {}).get("tg_api_hash"),
            tg_session_file=(user_creds or {}).get("tg_session_file"),
        )
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
        recip_ok, recip_err = await check_recipient_access(
            recip_id,
            tg_api_id=(user_creds or {}).get("tg_api_id"),
            tg_api_hash=(user_creds or {}).get("tg_api_hash"),
            tg_session_file=(user_creds or {}).get("tg_session_file"),
        )
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
        _delivery_importance = delivery_importance if delivery_importance in ("important", "informational") else "important"
        _delivery_send_file = delivery_send_file.lower() in ("1", "true", "yes", "on")
        _delivery_send_text = delivery_send_text.lower() in ("1", "true", "yes", "on")
        _delivery_text_max_chars = None
        if delivery_text_max_chars and str(delivery_text_max_chars).strip():
            try:
                _delivery_text_max_chars = int(delivery_text_max_chars.strip())
            except ValueError:
                pass
        _delivery_summary_only = delivery_summary_only.lower() in ("1", "true", "yes", "on")
        
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO web_channels (
                    user_id, telegram_chat_id, name, description, peer_type,
                    prompt_file, consolidated_doc_path, consolidated_doc_prompt_file,
                    poll_interval_minutes, enabled, recipient_telegram_id, recipient_name,
                    access_method, access_status,
                    delivery_importance, delivery_send_file, delivery_send_text,
                    delivery_text_max_chars, delivery_summary_only
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                access_status,
                _delivery_importance,
                _delivery_send_file,
                _delivery_send_text,
                _delivery_text_max_chars,
                _delivery_summary_only,
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
    user_telegram_id: Optional[str] = Form(None),
    name: Optional[str] = Form(None),
    recipient_telegram_id: Optional[int] = Form(None),
    recipient_name: Optional[str] = Form(None),
    delivery_importance: Optional[str] = Form(None),
    delivery_send_file: Optional[str] = Form(None),
    delivery_send_text: Optional[str] = Form(None),
    delivery_text_max_chars: Optional[str] = Form(None),
    delivery_summary_only: Optional[str] = Form(None),
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Обновляет канал пользователя (название, получатель, настройки доставки дайджеста)."""
    try:
        user_telegram_id_i = _parse_user_telegram_id(user_telegram_id)
        user_id = _resolve_user_id(db, current_user, user_telegram_id=user_telegram_id_i)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")
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
        if delivery_importance is not None and delivery_importance in ("important", "informational"):
            updates.append("delivery_importance = %s")
            params.append(delivery_importance)
        if delivery_send_file is not None:
            updates.append("delivery_send_file = %s")
            params.append(delivery_send_file.lower() in ("1", "true", "yes", "on"))
        if delivery_send_text is not None:
            updates.append("delivery_send_text = %s")
            params.append(delivery_send_text.lower() in ("1", "true", "yes", "on"))
        if delivery_text_max_chars is not None:
            try:
                v = int(delivery_text_max_chars.strip()) if delivery_text_max_chars.strip() else None
                updates.append("delivery_text_max_chars = %s")
                params.append(v)
            except ValueError:
                pass
        if delivery_summary_only is not None:
            updates.append("delivery_summary_only = %s")
            params.append(delivery_summary_only.lower() in ("1", "true", "yes", "on"))
        
        if not updates:
            raise HTTPException(status_code=400, detail="Не указаны поля для обновления")
        
        params.extend([channel_id, user_id])
        
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"""
                UPDATE web_channels 
                SET {", ".join(updates)}, updated_at = now()
                WHERE id = %s AND user_id = %s
                RETURNING id, name, recipient_telegram_id, recipient_name,
                    delivery_importance, delivery_send_file, delivery_send_text,
                    delivery_text_max_chars, delivery_summary_only
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
                "delivery_importance": row.get("delivery_importance") or "important",
                "delivery_send_file": row.get("delivery_send_file", True),
                "delivery_send_text": row.get("delivery_send_text", True),
                "delivery_text_max_chars": row.get("delivery_text_max_chars"),
                "delivery_summary_only": row.get("delivery_summary_only", False),
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
    user_telegram_id: Optional[str] = None,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Удаляет канал пользователя"""
    try:
        user_telegram_id_i = _parse_user_telegram_id(user_telegram_id)
        user_id = _resolve_user_id(db, current_user, user_telegram_id=user_telegram_id_i)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")
        
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
    user_telegram_id: Optional[str] = None,
    limit: int = 10,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Возвращает последние дайджесты канала"""
    try:
        user_telegram_id_i = _parse_user_telegram_id(user_telegram_id)
        user_id = _resolve_user_id(db, current_user, user_telegram_id=user_telegram_id_i)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")
        
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
    user_telegram_id: Optional[str] = None,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Возвращает сводный инженерный документ канала"""
    try:
        user_telegram_id_i = _parse_user_telegram_id(user_telegram_id)
        user_id = _resolve_user_id(db, current_user, user_telegram_id=user_telegram_id_i)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")
        
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
    user_telegram_id: Optional[str] = None,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Возвращает все каналы пользователя с их промптами (библиотека промптов)."""
    try:
        user_telegram_id_i = _parse_user_telegram_id(user_telegram_id)
        user_id = _resolve_user_id(db, current_user, user_telegram_id=user_telegram_id_i)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")
        
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
                    SELECT id, prompt_type, name, "text", is_default, created_at
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
                        "digest": [{"id": p["id"], "name": p["name"], "text": p.get("text") or "", "is_default": p["is_default"]} for p in digest_prompts],
                        "consolidated": [{"id": p["id"], "name": p["name"], "text": p.get("text") or "", "is_default": p["is_default"]} for p in consolidated_prompts],
                    }
                })
            
            return {"channels": result}
    except Exception as e:
        logger.error(f"Ошибка получения библиотеки промптов: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/prompt-library/templates", response_class=JSONResponse)
async def get_prompt_library_templates(
    prompt_type: Optional[str] = None,
    user_telegram_id: Optional[int] = None,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Возвращает шаблоны промптов из таблицы prompt_library (библиотека в БД)."""
    try:
        user_id = _resolve_user_id(db, current_user, user_telegram_id=user_telegram_id, create_from_telegram=False)
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            params = []
            where_parts = []
            if user_id:
                where_parts.append("(visibility = 'public' OR owner_user_id = %s)")
                params.append(user_id)
            else:
                where_parts.append("visibility = 'public'")
            if prompt_type:
                where_parts.append("prompt_type = %s")
                params.append(prompt_type)

            where_sql = " AND ".join(where_parts) if where_parts else "true"
            cur.execute(
                f"""
                SELECT id, name, prompt_type, file_path, body, created_at, updated_at,
                       owner_user_id, visibility, is_base
                FROM prompt_library
                WHERE {where_sql}
                ORDER BY prompt_type, name
                """,
                tuple(params),
            )
            rows = cur.fetchall()
            result = []
            for r in rows:
                result.append({
                    "id": r["id"],
                    "name": r["name"],
                    "prompt_type": r["prompt_type"],
                    "file_path": r.get("file_path"),
                    "body": r.get("body") or "",
                    "visibility": r.get("visibility") or "public",
                    "is_base": bool(r.get("is_base")),
                    "is_mine": bool(user_id and r.get("owner_user_id") == user_id),
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                    "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
                })
            return {"templates": result}
    except psycopg2.ProgrammingError as e:
        if "does not exist" in str(e).lower() or "relation" in str(e).lower():
            return {"templates": [], "message": "Таблица prompt_library/поля sharing не найдены. Выполните миграции 005 и 009."}
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
                        INSERT INTO prompt_library (name, prompt_type, file_path, body, owner_user_id, visibility, is_base)
                        VALUES (%s, %s, %s, %s, NULL, 'public', true)
                        ON CONFLICT (file_path) DO UPDATE SET
                            name = EXCLUDED.name,
                            prompt_type = EXCLUDED.prompt_type,
                            body = EXCLUDED.body,
                            owner_user_id = NULL,
                            visibility = 'public',
                            is_base = true,
                            updated_at = now()
                    """, (name, prompt_type, rel, body))
                    synced.append({"file_path": rel, "name": name, "prompt_type": prompt_type})
        db.commit()
        return {"synced": len(synced), "templates": synced}
    except psycopg2.ProgrammingError as e:
        if "does not exist" in str(e).lower():
            raise HTTPException(
                status_code=400,
                detail="Таблица prompt_library/поля sharing не найдены. Выполните миграции 005 и 009."
            )
        raise
    except Exception as e:
        logger.exception(f"Ошибка синхронизации prompt_library: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/prompt-library/templates", response_class=JSONResponse)
async def create_prompt_library_template(
    request: Request,
    body: PromptLibraryTemplateCreate,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Создаёт пользовательский шаблон в prompt_library с выбором public/private."""
    try:
        user_id = _resolve_user_id(db, current_user, user_telegram_id=body.user_telegram_id)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")
        if body.prompt_type not in ("digest", "consolidated"):
            raise HTTPException(status_code=400, detail="prompt_type должен быть digest или consolidated")
        if not body.name.strip():
            raise HTTPException(status_code=400, detail="name обязателен")
        if not body.body.strip():
            raise HTTPException(status_code=400, detail="body обязателен")

        visibility = "public" if body.share_to_library else "private"
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO prompt_library (
                    name, prompt_type, file_path, body, owner_user_id, visibility, is_base
                ) VALUES (%s, %s, NULL, %s, %s, %s, false)
                RETURNING id
                """,
                (body.name.strip(), body.prompt_type, body.body, user_id, visibility),
            )
            template_id = cur.fetchone()["id"]
        db.commit()

        audit_log(
            db, _audit_user_id(current_user), "prompt_library_created",
            {"template_id": template_id, "visibility": visibility}, request,
            "prompt_library", str(template_id),
        )
        return {"success": True, "template_id": template_id, "visibility": visibility}
    except HTTPException:
        raise
    except psycopg2.ProgrammingError as e:
        db.rollback()
        if "does not exist" in str(e).lower():
            raise HTTPException(status_code=400, detail="Выполните миграцию 009_user_runtime_and_prompt_sharing.sql.")
        raise
    except Exception as e:
        logger.error("Ошибка создания шаблона prompt_library: %s", e)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/prompt-library/templates/{template_id}/sharing", response_class=JSONResponse)
async def update_prompt_library_template_sharing(
    request: Request,
    template_id: int,
    body: PromptLibraryTemplateSharingUpdate,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Меняет видимость пользовательского шаблона prompt_library (public/private)."""
    try:
        user_id = _resolve_user_id(db, current_user, user_telegram_id=body.user_telegram_id)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")
        visibility = "public" if body.share_to_library else "private"

        with db.cursor() as cur:
            cur.execute(
                """
                UPDATE prompt_library
                SET visibility = %s, updated_at = now()
                WHERE id = %s
                  AND owner_user_id = %s
                  AND COALESCE(is_base, false) = false
                """,
                (visibility, template_id, user_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Шаблон не найден или нет прав на изменение")
        db.commit()

        audit_log(
            db, _audit_user_id(current_user), "prompt_library_sharing_updated",
            {"template_id": template_id, "visibility": visibility}, request,
            "prompt_library", str(template_id),
        )
        return {"success": True, "template_id": template_id, "visibility": visibility}
    except HTTPException:
        raise
    except psycopg2.ProgrammingError as e:
        db.rollback()
        if "does not exist" in str(e).lower():
            raise HTTPException(status_code=400, detail="Выполните миграцию 009_user_runtime_and_prompt_sharing.sql.")
        raise
    except Exception as e:
        logger.error("Ошибка изменения видимости шаблона prompt_library: %s", e)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channels/{channel_id}/prompts", response_class=JSONResponse)
async def get_channel_prompts(
    channel_id: int,
    user_telegram_id: Optional[str] = None,
    prompt_type: Optional[str] = None,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Возвращает промпты канала для редактирования"""
    try:
        user_telegram_id_i = _parse_user_telegram_id(user_telegram_id)
        user_id = _resolve_user_id(db, current_user, user_telegram_id=user_telegram_id_i)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")
        
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
    user_telegram_id: Optional[str] = Form(None),
    prompt_type: str = Form(...),
    name: str = Form(...),
    text: str = Form(...),
    is_default: bool = Form(False),
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Создаёт новый промпт для канала"""
    try:
        user_telegram_id_i = _parse_user_telegram_id(user_telegram_id)
        user_id = _resolve_user_id(db, current_user, user_telegram_id=user_telegram_id_i)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")
        
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
    user_telegram_id: Optional[str] = Form(None),
    name: Optional[str] = Form(None),
    text: Optional[str] = Form(None),
    is_default: Optional[bool] = Form(None),
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Обновляет промпт канала"""
    try:
        user_telegram_id_i = _parse_user_telegram_id(user_telegram_id)
        user_id = _resolve_user_id(db, current_user, user_telegram_id=user_telegram_id_i)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")
        
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
    user_telegram_id: Optional[str] = None,
    current_user: Optional[str] = Depends(get_current_auth_user),
    db=Depends(get_db),
):
    """Удаляет промпт канала"""
    try:
        user_telegram_id_i = _parse_user_telegram_id(user_telegram_id)
        user_id = _resolve_user_id(db, current_user, user_telegram_id=user_telegram_id_i)
        if not user_id:
            raise HTTPException(status_code=400, detail="Не удалось определить пользователя")
        
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
