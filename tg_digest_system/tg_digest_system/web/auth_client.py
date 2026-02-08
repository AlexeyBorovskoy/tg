"""
Клиент сервиса авторизации (asudd/services/auth).
Используется для проверки токена и доступа к маршрутам tg_digest_system.
"""
import os
import logging
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

AUTH_SERVICE_URL = os.environ.get("AUTH_SERVICE_URL", "").rstrip("/")
AUTH_CHECK_ENABLED = os.environ.get("AUTH_CHECK_ENABLED", "0").lower() in ("1", "true", "yes")


async def login(username: str, password: str, sid: str = "default_session") -> Optional[Tuple[str, str]]:
    """
    Вход через auth-сервис. Возвращает (access_token, refresh_token) или None при ошибке.
    """
    if not AUTH_SERVICE_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{AUTH_SERVICE_URL}/api/v1/auth/login",
                data={"username": username, "password": password},
                headers={"sid": sid},
            )
            if r.status_code != 200:
                logger.warning("Auth login failed: %s %s", r.status_code, r.text[:200])
                return None
            data = r.json()
            return (data.get("access_token"), data.get("refresh_token"))
    except Exception as e:
        logger.warning("Auth login error: %s", e)
        return None


async def check_token(access_token: str, path: str) -> Tuple[bool, Optional[str]]:
    """
    Проверка доступа к path через auth /check.
    Возвращает (allowed, username). username = None при отказе.
    """
    if not AUTH_SERVICE_URL or not access_token:
        return False, None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{AUTH_SERVICE_URL}/api/v1/auth/check",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Original-URI": path,
                },
            )
            if r.status_code == 200:
                # username можно получить из /me при необходимости
                username = await get_username(access_token)
                return True, username
            if r.status_code == 401:
                return False, None
            if r.status_code == 403:
                return False, None
            logger.warning("Auth check unexpected: %s %s", r.status_code, r.text[:200])
            return False, None
    except Exception as e:
        logger.warning("Auth check error: %s", e)
        return False, None


async def get_username(access_token: str) -> Optional[str]:
    """Получить логин текущего пользователя через /me."""
    if not AUTH_SERVICE_URL or not access_token:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{AUTH_SERVICE_URL}/api/v1/auth/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code != 200:
                return None
            data = r.json()
            return data.get("login")
    except Exception as e:
        logger.debug("Auth get_me error: %s", e)
        return None


def token_from_header(authorization: Optional[str]) -> Optional[str]:
    """Извлечь Bearer токен из заголовка Authorization."""
    if not authorization:
        return None
    s = authorization.strip()
    if s.lower().startswith("bearer "):
        return s[7:].strip()
    return s.strip()
