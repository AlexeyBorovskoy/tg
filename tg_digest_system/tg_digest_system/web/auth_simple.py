#!/usr/bin/env python3
"""
auth_simple.py — Простая система идентификации через cookie сессии
"""

import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import Request, HTTPException, Depends
from fastapi.responses import Response

logger = logging.getLogger(__name__)


def get_db():
    """Получает подключение к БД (из web_api.py)"""
    import os
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


def create_session(user_id: int, db, expires_days: int = 30) -> str:
    """
    Создаёт сессию для пользователя.
    
    Args:
        user_id: ID пользователя
        db: Подключение к БД
        expires_days: Срок действия сессии в днях
    
    Returns:
        session_token: Токен сессии
    """
    session_token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(days=expires_days)
    
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO user_sessions (user_id, session_token, expires_at)
            VALUES (%s, %s, %s)
        """, (user_id, session_token, expires_at))
        db.commit()
    
    logger.info(f"Создана сессия для user_id={user_id}, expires_at={expires_at}")
    return session_token


def get_user_from_session(session_token: str, db) -> Optional[int]:
    """
    Получает user_id по токену сессии.
    
    Args:
        session_token: Токен сессии
        db: Подключение к БД
    
    Returns:
        user_id или None если сессия невалидна
    """
    if not session_token:
        return None
    
    with db.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT user_id FROM user_sessions
            WHERE session_token = %s 
            AND expires_at > now()
        """, (session_token,))
        
        row = cur.fetchone()
        if row:
            # Обновляем время последнего использования
            cur.execute("""
                UPDATE user_sessions 
                SET last_used_at = now()
                WHERE session_token = %s
            """, (session_token,))
            db.commit()
            return row['user_id']
    
    return None


def delete_session(session_token: str, db) -> None:
    """
    Удаляет сессию.
    
    Args:
        session_token: Токен сессии
        db: Подключение к БД
    """
    with db.cursor() as cur:
        cur.execute("DELETE FROM user_sessions WHERE session_token = %s", (session_token,))
        db.commit()
    logger.info(f"Удалена сессия: {session_token[:10]}...")


def cleanup_expired_sessions(db) -> int:
    """
    Очищает истекшие сессии.
    
    Args:
        db: Подключение к БД
    
    Returns:
        Количество удалённых сессий
    """
    with db.cursor() as cur:
        cur.execute("SELECT cleanup_expired_sessions()")
        deleted_count = cur.fetchone()[0]
        db.commit()
    
    logger.info(f"Очищено истекших сессий: {deleted_count}")
    return deleted_count


# Dependency для FastAPI
async def get_current_user(
    request: Request,
    db=Depends(get_db)
) -> Optional[int]:
    """
    Получает текущего пользователя из сессии.
    Используется как dependency в endpoints.
    
    Returns:
        user_id или None если пользователь не авторизован
    """
    session_token = request.cookies.get("session_token")
    if not session_token:
        return None
    
    return get_user_from_session(session_token, db)


def require_auth(current_user: Optional[int] = Depends(get_current_user)) -> int:
    """
    Dependency который требует авторизации.
    Вызывает HTTPException если пользователь не авторизован.
    
    Returns:
        user_id
    """
    if not current_user:
        raise HTTPException(
            status_code=401,
            detail="Требуется авторизация. Пожалуйста, введите ваш Telegram ID."
        )
    return current_user


def set_session_cookie(response: Response, session_token: str, expires_days: int = 30) -> None:
    """
    Устанавливает cookie с токеном сессии.
    
    Args:
        response: FastAPI Response объект
        session_token: Токен сессии
        expires_days: Срок действия в днях
    """
    max_age = expires_days * 24 * 60 * 60
    response.set_cookie(
        key="session_token",
        value=session_token,
        max_age=max_age,
        httponly=True,  # Защита от XSS
        secure=False,   # Для тестирования (в продакшене должно быть True если используется HTTPS)
        samesite="lax"  # Защита от CSRF
    )


def clear_session_cookie(response: Response) -> None:
    """Очищает cookie сессии"""
    response.delete_cookie(key="session_token")
