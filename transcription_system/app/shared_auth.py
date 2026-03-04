from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from .settings import settings

logger = logging.getLogger(__name__)

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except Exception:  # pragma: no cover - optional dependency
    psycopg2 = None
    RealDictCursor = None


class SharedAuthError(RuntimeError):
    pass


def _normalize_login(login: str) -> str:
    return (login or "").strip().lower()


def _password_verify(password: str, encoded: str) -> bool:
    try:
        algo, rounds_s, salt_hex, digest_hex = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
    except Exception:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), rounds).hex()
    return hmac.compare_digest(digest, digest_hex)


def _password_hash(password: str) -> str:
    rounds = 240000
    salt_hex = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), rounds).hex()
    return f"pbkdf2_sha256${rounds}${salt_hex}${digest}"


class PostgresSharedAuth:
    """
    Shared auth backend for TG Digest schema:
    - user_sessions(session_token, user_id, expires_at, last_used_at)
    - user_local_auth(user_id, login, password_hash, is_active)
    - users(id, is_active)
    """

    def __init__(self) -> None:
        self.enabled = bool(settings.auth_shared_enabled)
        self.available = bool(self.enabled and psycopg2 is not None and RealDictCursor is not None)
        if self.enabled and not self.available:
            logger.warning("AUTH_SHARED_ENABLED=1 but psycopg2 is not installed. Shared auth is disabled.")

    @staticmethod
    def _role_for_login(login: str) -> str:
        return "admin" if _normalize_login(login) == _normalize_login(settings.auth_shared_admin_login) else "user"

    def _connect(self):
        if not self.available:
            raise SharedAuthError("shared auth unavailable")
        try:
            return psycopg2.connect(
                host=settings.auth_shared_pg_host,
                port=settings.auth_shared_pg_port,
                database=settings.auth_shared_pg_database,
                user=settings.auth_shared_pg_user,
                password=settings.auth_shared_pg_password,
            )
        except Exception as exc:  # pragma: no cover - depends on runtime env
            raise SharedAuthError(f"shared auth DB connection failed: {exc}") from exc

    def get_user_by_session(self, token: str) -> dict[str, Any] | None:
        token_s = (token or "").strip()
        if not token_s or not self.available:
            return None

        conn = None
        try:
            conn = self._connect()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT us.user_id, ula.login, COALESCE(u.is_active, true) AS user_active, ula.is_active AS auth_active
                    FROM user_sessions us
                    JOIN user_local_auth ula ON ula.user_id = us.user_id
                    JOIN users u ON u.id = us.user_id
                    WHERE us.session_token = %s
                      AND us.expires_at > now()
                    LIMIT 1
                    """,
                    (token_s,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                if not bool(row.get("user_active")) or not bool(row.get("auth_active")):
                    return None
                cur.execute("UPDATE user_sessions SET last_used_at = now() WHERE session_token = %s", (token_s,))
            conn.commit()
            login = str(row["login"])
            return {
                "id": int(row["user_id"]),
                "login": login,
                "role": self._role_for_login(login),
                "is_active": True,
            }
        except Exception as exc:
            logger.warning("Shared auth get_user_by_session failed: %s", exc)
            if conn:
                conn.rollback()
            return None
        finally:
            if conn:
                conn.close()

    def authenticate_local(self, login: str, password: str) -> dict[str, Any] | None:
        login_norm = _normalize_login(login)
        if not login_norm or not password or not self.available:
            return None

        conn = None
        try:
            conn = self._connect()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT ula.user_id, ula.login, ula.password_hash, ula.is_active AS auth_active, COALESCE(u.is_active, true) AS user_active
                    FROM user_local_auth ula
                    JOIN users u ON u.id = ula.user_id
                    WHERE ula.login = %s
                    LIMIT 1
                    """,
                    (login_norm,),
                )
                row = cur.fetchone()
            if not row:
                return None
            if not bool(row.get("auth_active")) or not bool(row.get("user_active")):
                return None
            if not _password_verify(password, str(row.get("password_hash") or "")):
                return None
            login_real = str(row["login"])
            return {
                "id": int(row["user_id"]),
                "login": login_real,
                "role": self._role_for_login(login_real),
                "is_active": True,
            }
        except Exception as exc:
            logger.warning("Shared auth login failed: %s", exc)
            return None
        finally:
            if conn:
                conn.close()

    def create_session(self, user_id: int, days: int | None = None) -> str:
        if not self.available:
            raise SharedAuthError("shared auth unavailable")
        ttl_days = max(1, int(days or settings.auth_session_days or 14))
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)

        conn = None
        try:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_sessions (user_id, session_token, expires_at)
                    VALUES (%s, %s, %s)
                    """,
                    (int(user_id), token, expires_at),
                )
            conn.commit()
            return token
        except Exception as exc:
            if conn:
                conn.rollback()
            raise SharedAuthError(f"shared auth create_session failed: {exc}") from exc
        finally:
            if conn:
                conn.close()

    def delete_session(self, token: str) -> None:
        token_s = (token or "").strip()
        if not token_s or not self.available:
            return
        conn = None
        try:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_sessions WHERE session_token = %s", (token_s,))
            conn.commit()
        except Exception as exc:
            logger.warning("Shared auth delete_session failed: %s", exc)
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    def list_users(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.available:
            return []
        conn = None
        try:
            conn = self._connect()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT ula.user_id AS id, ula.login, ula.is_active AS auth_active, COALESCE(u.is_active, true) AS user_active,
                           u.created_at
                    FROM user_local_auth ula
                    JOIN users u ON u.id = ula.user_id
                    ORDER BY ula.login ASC
                    LIMIT %s
                    """,
                    (max(1, int(limit)),),
                )
                rows = cur.fetchall() or []
            out: list[dict[str, Any]] = []
            for row in rows:
                login = str(row["login"])
                out.append(
                    {
                        "id": int(row["id"]),
                        "login": login,
                        "role": self._role_for_login(login),
                        "is_active": bool(row.get("auth_active")) and bool(row.get("user_active")),
                        "created_at": row.get("created_at"),
                        "updated_at": None,
                    }
                )
            return out
        except Exception as exc:
            logger.warning("Shared auth list_users failed: %s", exc)
            return []
        finally:
            if conn:
                conn.close()

    def create_user(self, login: str, password: str) -> dict[str, Any]:
        """
        Optional registration path for shared auth.
        Requires users.telegram_id to be nullable (migration 007 in TG Digest).
        """
        login_norm = _normalize_login(login)
        if not login_norm:
            raise SharedAuthError("login is required")
        if not password:
            raise SharedAuthError("password is required")
        if not self.available:
            raise SharedAuthError("shared auth unavailable")

        conn = None
        try:
            conn = self._connect()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT user_id FROM user_local_auth WHERE login = %s LIMIT 1", (login_norm,))
                exists = cur.fetchone()
                if exists:
                    raise SharedAuthError("login already exists")

                cur.execute(
                    """
                    INSERT INTO users (name, is_active)
                    VALUES (%s, true)
                    RETURNING id
                    """,
                    (login_norm,),
                )
                user_row = cur.fetchone()
                if not user_row:
                    raise SharedAuthError("failed to create user")
                user_id = int(user_row["id"])

                cur.execute(
                    """
                    INSERT INTO user_local_auth (user_id, login, password_hash, is_active)
                    VALUES (%s, %s, %s, true)
                    """,
                    (user_id, login_norm, _password_hash(password)),
                )
            conn.commit()
            return {
                "id": user_id,
                "login": login_norm,
                "role": self._role_for_login(login_norm),
                "is_active": True,
            }
        except SharedAuthError:
            if conn:
                conn.rollback()
            raise
        except Exception as exc:
            if conn:
                conn.rollback()
            raise SharedAuthError(f"shared auth create_user failed: {exc}") from exc
        finally:
            if conn:
                conn.close()
