"""
Собственная авторизация: OAuth (Google/Яндекс) + свои JWT.
Наш сервис сам делает OAuth и выдаёт свои токены.
"""
import os
import logging
import secrets
import time
from typing import Optional, Tuple
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Конфигурация
# -----------------------------------------------------------------------------
AUTH_OWN_ENABLED = os.environ.get("AUTH_OWN_ENABLED", "0").lower() in ("1", "true", "yes")
JWT_SECRET = os.environ.get("JWT_SECRET", "").encode("utf-8") or secrets.token_bytes(32)
JWT_ALGORITHM = "HS256"
JWT_ACCESS_EXPIRES_SEC = int(os.environ.get("JWT_ACCESS_EXPIRES_SEC", "3600"))  # 1 ч

# Базовый URL приложения (для redirect_uri OAuth)
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/")

# Google OAuth 2.0
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Yandex OAuth 2.0
YANDEX_CLIENT_ID = os.environ.get("YANDEX_OAUTH_CLIENT_ID", "")
YANDEX_CLIENT_SECRET = os.environ.get("YANDEX_OAUTH_CLIENT_SECRET", "")
YANDEX_AUTH_URL = "https://oauth.yandex.com/authorize"
YANDEX_TOKEN_URL = "https://oauth.yandex.com/token"
YANDEX_USERINFO_URL = "https://login.yandex.ru/info?format=json"


@dataclass
class AuthUser:
    """Текущий пользователь после проверки токена."""
    user_id: int
    email: str
    display_name: Optional[str] = None

    def __str__(self) -> str:
        return self.email


# -----------------------------------------------------------------------------
# JWT
# -----------------------------------------------------------------------------
def create_access_token(user_id: int, email: str, display_name: Optional[str] = None) -> str:
    """Создаёт наш JWT access token."""
    try:
        import jwt
    except ImportError:
        logger.warning("PyJWT not installed; pip install PyJWT")
        return ""
    payload = {
        "sub": str(user_id),
        "email": email,
        "name": display_name or email,
        "type": "access",
        "exp": int(time.time()) + JWT_ACCESS_EXPIRES_SEC,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_access_token(token: str) -> Optional[AuthUser]:
    """Проверяет наш JWT и возвращает AuthUser или None."""
    if not token or not JWT_SECRET:
        return None
    try:
        import jwt
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            return None
        user_id = int(payload.get("sub", 0))
        email = payload.get("email") or ""
        if not user_id or not email:
            return None
        return AuthUser(
            user_id=user_id,
            email=email,
            display_name=payload.get("name"),
        )
    except Exception as e:
        logger.debug("JWT verify error: %s", e)
        return None


# -----------------------------------------------------------------------------
# OAuth: URL для редиректа на провайдера
# -----------------------------------------------------------------------------
def get_google_authorize_url(state: str, redirect_uri: str, scope: str = "openid email profile") -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return GOOGLE_AUTH_URL + "?" + urlencode(params)


def get_yandex_authorize_url(state: str, redirect_uri: str) -> str:
    params = {
        "response_type": "code",
        "client_id": YANDEX_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return YANDEX_AUTH_URL + "?" + urlencode(params)


# -----------------------------------------------------------------------------
# OAuth: обмен code на токен и получение данных пользователя
# -----------------------------------------------------------------------------
async def exchange_google_code(code: str, redirect_uri: str) -> Optional[Tuple[str, str, str]]:
    """
    Обменивает code на access_token и возвращает (external_id, email, display_name) или None.
    """
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code != 200:
                logger.warning("Google token exchange failed: %s %s", r.status_code, r.text[:200])
                return None
            data = r.json()
            access_token = data.get("access_token")
            if not access_token:
                return None
            # Userinfo
            r2 = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r2.status_code != 200:
                return None
            ui = r2.json()
            external_id = ui.get("id") or ""
            email = ui.get("email") or ""
            display_name = (ui.get("name") or "").strip() or email
            return (str(external_id), email, display_name)
    except Exception as e:
        logger.warning("Google OAuth error: %s", e)
        return None


async def exchange_yandex_code(code: str, redirect_uri: str) -> Optional[Tuple[str, str, str]]:
    """
    Обменивает code на access_token и возвращает (external_id, email, display_name) или None.
    """
    if not YANDEX_CLIENT_ID or not YANDEX_CLIENT_SECRET:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                YANDEX_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": YANDEX_CLIENT_ID,
                    "client_secret": YANDEX_CLIENT_SECRET,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code != 200:
                logger.warning("Yandex token exchange failed: %s %s", r.status_code, r.text[:200])
                return None
            data = r.json()
            access_token = data.get("access_token")
            if not access_token:
                return None
            r2 = await client.get(
                YANDEX_USERINFO_URL,
                headers={"Authorization": f"OAuth {access_token}"},
            )
            if r2.status_code != 200:
                return None
            ui = r2.json()
            external_id = ui.get("id") or ""
            email = (ui.get("default_email") or ui.get("emails", [""])[0] or "").strip()
            display_name = (ui.get("real_name") or ui.get("display_name") or email or "").strip()
            return (str(external_id), email, display_name)
    except Exception as e:
        logger.warning("Yandex OAuth error: %s", e)
        return None


def token_from_header(authorization: Optional[str]) -> Optional[str]:
    """Извлечь Bearer токен из заголовка Authorization."""
    if not authorization:
        return None
    s = authorization.strip()
    if s.lower().startswith("bearer "):
        return s[7:].strip()
    return s.strip()
