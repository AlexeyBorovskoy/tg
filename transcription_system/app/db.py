from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .settings import settings


ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime(ISO_FMT)


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, ISO_FMT).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _password_hash(password: str, salt: str | None = None) -> str:
    salt_hex = salt or secrets.token_hex(16)
    rounds = 200_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_hex.encode("utf-8"), rounds)
    return f"pbkdf2_sha256${rounds}${salt_hex}${digest.hex()}"


def _password_verify(password: str, encoded: str) -> bool:
    try:
        algo, rounds_s, salt_hex, digest_hex = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
    except Exception:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_hex.encode("utf-8"), rounds).hex()
    return hmac.compare_digest(digest, digest_hex)


@dataclass
class JobCreate:
    id: str
    user_id: int
    input_filename: str
    upload_path: str
    provider: str
    diarization: bool
    speakers_expected: int | None
    source_kind: str
    llm_enabled: bool
    llm_profile: str
    llm_model_override: str | None
    prompt_id: int | None
    roles_enabled: bool


class SqliteStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def init_db(self) -> None:
        with self.conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    source_kind TEXT NOT NULL DEFAULT 'meeting',
                    status TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    input_filename TEXT NOT NULL,
                    upload_path TEXT NOT NULL,
                    diarization INTEGER NOT NULL DEFAULT 1,
                    speakers_expected INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    transcript_text TEXT,
                    transcript_md TEXT,
                    raw_result_json TEXT,
                    speaker_map_json TEXT,
                    error_message TEXT,
                    llm_enabled INTEGER NOT NULL DEFAULT 1,
                    llm_profile TEXT NOT NULL DEFAULT 'auto',
                    llm_model_override TEXT,
                    llm_status TEXT,
                    llm_model_used TEXT,
                    llm_error TEXT,
                    llm_duration_ms INTEGER,
                    llm_output_text TEXT,
                    prompt_id INTEGER,
                    prompt_name_used TEXT,
                    roles_enabled INTEGER NOT NULL DEFAULT 1,
                    roles_status TEXT,
                    roles_model_used TEXT,
                    roles_error TEXT,
                    roles_duration_ms INTEGER,
                    speaker_map_source TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
                CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    login TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_local_auth (
                    user_id INTEGER PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_user_sessions_exp ON user_sessions(expires_at);

                CREATE TABLE IF NOT EXISTS glossary_terms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wrong TEXT NOT NULL,
                    correct TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (wrong)
                );

                CREATE TABLE IF NOT EXISTS prompt_library (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    profile TEXT NOT NULL,
                    system_prompt TEXT NOT NULL,
                    user_template TEXT NOT NULL,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_prompt_library_profile ON prompt_library(profile);
                CREATE INDEX IF NOT EXISTS idx_prompt_library_default ON prompt_library(profile, is_default);

                CREATE TABLE IF NOT EXISTS job_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    seq_no INTEGER NOT NULL,
                    start_ms INTEGER,
                    end_ms INTEGER,
                    speaker TEXT,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_job_segments_job ON job_segments(job_id, seq_no);

                CREATE TABLE IF NOT EXISTS job_glossary_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    wrong TEXT NOT NULL,
                    correct TEXT NOT NULL,
                    replace_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_job_glossary_stats_job ON job_glossary_stats(job_id);

                CREATE TABLE IF NOT EXISTS job_protocols (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'generated',
                    protocol_md TEXT NOT NULL,
                    protocol_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._ensure_jobs_columns(c)
            self._seed_default_prompts(c)
            self._seed_admin_user(c)
            self._cleanup_expired_sessions(c)

    @staticmethod
    def _ensure_jobs_columns(c: sqlite3.Connection) -> None:
        rows = c.execute("PRAGMA table_info(jobs)").fetchall()
        columns = {str(r[1]) for r in rows}
        additions = [
            ("user_id", "INTEGER"),
            ("llm_enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("llm_profile", "TEXT NOT NULL DEFAULT 'auto'"),
            ("llm_model_override", "TEXT"),
            ("llm_status", "TEXT"),
            ("llm_model_used", "TEXT"),
            ("llm_error", "TEXT"),
            ("llm_duration_ms", "INTEGER"),
            ("llm_output_text", "TEXT"),
            ("prompt_id", "INTEGER"),
            ("prompt_name_used", "TEXT"),
            ("roles_enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("roles_status", "TEXT"),
            ("roles_model_used", "TEXT"),
            ("roles_error", "TEXT"),
            ("roles_duration_ms", "INTEGER"),
            ("speaker_map_source", "TEXT"),
        ]
        for name, ddl in additions:
            if name not in columns:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {name} {ddl}")
        c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id, created_at DESC)")

    @staticmethod
    def _seed_default_prompts(c: sqlite3.Connection) -> None:
        count = c.execute("SELECT COUNT(*) FROM prompt_library").fetchone()[0]
        if count > 0:
            return

        ts = now_utc()
        defaults = [
            (
                "voice_default",
                "voice",
                (
                    "Ты редактор транскриптов на русском языке. Улучши читаемость, орфографию и пунктуацию "
                    "без искажения смысла. Не добавляй новые факты. Верни только итоговый текст."
                ),
                "Исправь transcript:\n\n{transcript}\n\nВерни только очищенный текст.",
                1,
                1,
                ts,
                ts,
            ),
            (
                "meeting_default",
                "meeting",
                (
                    "Ты редактор протоколов совещаний. Исправь transcript и сохрани структуру строк с таймкодами "
                    "и ролями, если она присутствует. Не добавляй новые факты."
                ),
                "Исправь transcript:\n\n{transcript}\n\nВерни только transcript без комментариев.",
                1,
                1,
                ts,
                ts,
            ),
        ]
        c.executemany(
            """
            INSERT INTO prompt_library (
                name, profile, system_prompt, user_template, is_default, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            defaults,
        )

    @staticmethod
    def _cleanup_expired_sessions(c: sqlite3.Connection) -> None:
        c.execute("DELETE FROM user_sessions WHERE expires_at<?", (now_utc(),))

    @staticmethod
    def _seed_admin_user(c: sqlite3.Connection) -> None:
        login = (settings.admin_login or "").strip().lower()
        password = (settings.admin_password or "").strip()
        if not login:
            return

        ts = now_utc()
        row = c.execute("SELECT id, role, login FROM users WHERE lower(login)=?", (login,)).fetchone()
        if row:
            user_id = int(row[0])
            role = str(row[1] or "user")
            old_login = str(row[2] or "")
            if role != "admin":
                c.execute("UPDATE users SET role='admin', updated_at=? WHERE id=?", (ts, user_id))
            if old_login != login:
                c.execute("UPDATE users SET login=?, updated_at=? WHERE id=?", (login, ts, user_id))
        else:
            cur = c.execute(
                """
                INSERT INTO users (login, role, is_active, created_at, updated_at)
                VALUES (?, 'admin', 1, ?, ?)
                """,
                (login, ts, ts),
            )
            user_id = int(cur.lastrowid)

        if password:
            auth_row = c.execute(
                "SELECT user_id FROM user_local_auth WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if auth_row:
                c.execute(
                    """
                    UPDATE user_local_auth
                    SET password_hash=?, updated_at=?
                    WHERE user_id=?
                    """,
                    (_password_hash(password), ts, user_id),
                )
            else:
                c.execute(
                    """
                    INSERT INTO user_local_auth (user_id, password_hash, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (user_id, _password_hash(password), ts),
                )

    def create_user(self, login: str, password: str, role: str = "user", is_active: bool = True) -> dict[str, Any]:
        login_norm = (login or "").strip().lower()
        if not login_norm:
            raise ValueError("login is required")
        if not password:
            raise ValueError("password is required")
        if role not in {"admin", "user"}:
            raise ValueError("role must be admin or user")

        ts = now_utc()
        with self.conn() as c:
            exists = c.execute("SELECT id FROM users WHERE lower(login)=?", (login_norm,)).fetchone()
            if exists:
                raise ValueError("login already exists")

            cur = c.execute(
                """
                INSERT INTO users (login, role, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (login_norm, role, 1 if is_active else 0, ts, ts),
            )
            user_id = int(cur.lastrowid)
            c.execute(
                """
                INSERT INTO user_local_auth (user_id, password_hash, updated_at)
                VALUES (?, ?, ?)
                """,
                (user_id, _password_hash(password), ts),
            )
            row = c.execute(
                """
                SELECT id, login, role, is_active, created_at, updated_at
                FROM users WHERE id=?
                """,
                (user_id,),
            ).fetchone()
            return dict(row) if row else {}

    def get_user_by_login(self, login: str) -> dict[str, Any] | None:
        login_norm = (login or "").strip().lower()
        if not login_norm:
            return None
        with self.conn() as c:
            row = c.execute(
                """
                SELECT id, login, role, is_active, created_at, updated_at
                FROM users
                WHERE lower(login)=?
                LIMIT 1
                """,
                (login_norm,),
            ).fetchone()
            return dict(row) if row else None

    def authenticate_local(self, login: str, password: str) -> dict[str, Any] | None:
        login_norm = (login or "").strip().lower()
        if not login_norm or not password:
            return None
        with self.conn() as c:
            row = c.execute(
                """
                SELECT u.id, u.login, u.role, u.is_active, u.created_at, u.updated_at, a.password_hash
                FROM users u
                JOIN user_local_auth a ON a.user_id = u.id
                WHERE lower(u.login)=?
                LIMIT 1
                """,
                (login_norm,),
            ).fetchone()
            if not row:
                return None
            if not bool(row["is_active"]):
                return None
            if not _password_verify(password, str(row["password_hash"] or "")):
                return None
            return {
                "id": int(row["id"]),
                "login": str(row["login"]),
                "role": str(row["role"] or "user"),
                "is_active": bool(row["is_active"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def create_session(self, user_id: int, days: int | None = None) -> str:
        ttl_days = max(1, int(days or settings.auth_session_days or 14))
        token = secrets.token_urlsafe(32)
        created = datetime.now(timezone.utc)
        expires = created + timedelta(days=ttl_days)
        created_s = created.strftime(ISO_FMT)
        expires_s = expires.strftime(ISO_FMT)
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO user_sessions (token, user_id, created_at, expires_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token, user_id, created_s, expires_s, created_s),
            )
        return token

    def get_user_by_session(self, token: str) -> dict[str, Any] | None:
        token_s = (token or "").strip()
        if not token_s:
            return None
        now_dt = datetime.now(timezone.utc)
        now_s = now_dt.strftime(ISO_FMT)
        with self.conn() as c:
            row = c.execute(
                """
                SELECT s.user_id, s.expires_at, u.login, u.role, u.is_active
                FROM user_sessions s
                JOIN users u ON u.id=s.user_id
                WHERE s.token=?
                LIMIT 1
                """,
                (token_s,),
            ).fetchone()
            if not row:
                return None
            expires_dt = _parse_iso_utc(row["expires_at"])
            if not expires_dt or expires_dt <= now_dt:
                c.execute("DELETE FROM user_sessions WHERE token=?", (token_s,))
                return None
            if not bool(row["is_active"]):
                return None
            c.execute("UPDATE user_sessions SET last_seen_at=? WHERE token=?", (now_s, token_s))
            return {
                "id": int(row["user_id"]),
                "login": str(row["login"]),
                "role": str(row["role"] or "user"),
                "is_active": bool(row["is_active"]),
                "session_expires_at": row["expires_at"],
            }

    def delete_session(self, token: str) -> None:
        token_s = (token or "").strip()
        if not token_s:
            return
        with self.conn() as c:
            c.execute("DELETE FROM user_sessions WHERE token=?", (token_s,))

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute(
                """
                SELECT id, login, role, is_active, created_at, updated_at
                FROM users WHERE id=?
                """,
                (user_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                """
                SELECT id, login, role, is_active, created_at, updated_at
                FROM users
                ORDER BY role DESC, login ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def create_job(self, payload: JobCreate) -> None:
        ts = now_utc()
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO jobs (
                    id, user_id, source_kind, status, provider, input_filename, upload_path,
                    diarization, speakers_expected, created_at, updated_at,
                    llm_enabled, llm_profile, llm_model_override, prompt_id, roles_enabled
                ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.id,
                    payload.user_id,
                    payload.source_kind,
                    payload.provider,
                    payload.input_filename,
                    payload.upload_path,
                    1 if payload.diarization else 0,
                    payload.speakers_expected,
                    ts,
                    ts,
                    1 if payload.llm_enabled else 0,
                    payload.llm_profile,
                    payload.llm_model_override,
                    payload.prompt_id,
                    1 if payload.roles_enabled else 0,
                ),
            )

    def set_job_running(self, job_id: str) -> None:
        ts = now_utc()
        with self.conn() as c:
            c.execute(
                "UPDATE jobs SET status='running', started_at=?, updated_at=? WHERE id=?",
                (ts, ts, job_id),
            )

    def set_job_done(
        self,
        job_id: str,
        transcript_text: str,
        transcript_md: str,
        raw_result: dict[str, Any],
        llm_status: str = "skipped",
        llm_model_used: str | None = None,
        llm_error: str | None = None,
        llm_duration_ms: int | None = None,
        llm_output_text: str | None = None,
        prompt_name_used: str | None = None,
        roles_status: str | None = None,
        roles_model_used: str | None = None,
        roles_error: str | None = None,
        roles_duration_ms: int | None = None,
        speaker_map_source: str | None = None,
    ) -> None:
        ts = now_utc()
        with self.conn() as c:
            c.execute(
                """
                UPDATE jobs
                SET status='done', transcript_text=?, transcript_md=?, raw_result_json=?,
                    finished_at=?, updated_at=?,
                    llm_status=?, llm_model_used=?, llm_error=?, llm_duration_ms=?, llm_output_text=?,
                    prompt_name_used=?, roles_status=?, roles_model_used=?, roles_error=?, roles_duration_ms=?,
                    speaker_map_source=?
                WHERE id=?
                """,
                (
                    transcript_text,
                    transcript_md,
                    json.dumps(raw_result, ensure_ascii=False),
                    ts,
                    ts,
                    llm_status,
                    llm_model_used,
                    llm_error,
                    llm_duration_ms,
                    llm_output_text,
                    prompt_name_used,
                    roles_status,
                    roles_model_used,
                    roles_error,
                    roles_duration_ms,
                    speaker_map_source,
                    job_id,
                ),
            )

    def set_job_error(self, job_id: str, error_message: str) -> None:
        ts = now_utc()
        with self.conn() as c:
            c.execute(
                "UPDATE jobs SET status='error', error_message=?, finished_at=?, updated_at=? WHERE id=?",
                (error_message, ts, ts, job_id),
            )

    def set_speaker_map(self, job_id: str, speaker_map: dict[str, str], source: str | None = None) -> None:
        ts = now_utc()
        with self.conn() as c:
            if source:
                c.execute(
                    "UPDATE jobs SET speaker_map_json=?, speaker_map_source=?, updated_at=? WHERE id=?",
                    (json.dumps(speaker_map, ensure_ascii=False), source, ts, job_id),
                )
            else:
                c.execute(
                    "UPDATE jobs SET speaker_map_json=?, updated_at=? WHERE id=?",
                    (json.dumps(speaker_map, ensure_ascii=False), ts, job_id),
                )

    def update_job_transcript(self, job_id: str, transcript_text: str, transcript_md: str) -> None:
        ts = now_utc()
        with self.conn() as c:
            c.execute(
                "UPDATE jobs SET transcript_text=?, transcript_md=?, updated_at=? WHERE id=?",
                (transcript_text, transcript_md, ts, job_id),
            )

    def replace_job_segments(self, job_id: str, utterances: list[dict[str, Any]]) -> None:
        ts = now_utc()
        with self.conn() as c:
            c.execute("DELETE FROM job_segments WHERE job_id=?", (job_id,))
            rows: list[tuple[Any, ...]] = []
            for idx, utt in enumerate(utterances):
                if not isinstance(utt, dict):
                    continue
                text = str(utt.get("text") or "").strip()
                if not text:
                    continue
                start_ms = utt.get("start")
                end_ms = utt.get("end")
                speaker = str(utt.get("speaker") or "").strip() or "UNKNOWN"
                rows.append((job_id, idx, start_ms, end_ms, speaker, text, ts))
            if rows:
                c.executemany(
                    """
                    INSERT INTO job_segments (
                        job_id, seq_no, start_ms, end_ms, speaker, text, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )

    def list_job_segments(self, job_id: str) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                """
                SELECT seq_no, start_ms, end_ms, speaker, text
                FROM job_segments
                WHERE job_id=?
                ORDER BY seq_no ASC
                """,
                (job_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def replace_job_glossary_stats(self, job_id: str, stats: list[dict[str, Any]]) -> None:
        ts = now_utc()
        with self.conn() as c:
            c.execute("DELETE FROM job_glossary_stats WHERE job_id=?", (job_id,))
            rows: list[tuple[Any, ...]] = []
            for item in stats:
                if not isinstance(item, dict):
                    continue
                wrong = str(item.get("wrong") or "").strip()
                correct = str(item.get("correct") or "").strip()
                count = int(item.get("count") or 0)
                if not wrong or not correct or count <= 0:
                    continue
                rows.append((job_id, wrong, correct, count, ts))
            if rows:
                c.executemany(
                    """
                    INSERT INTO job_glossary_stats (
                        job_id, wrong, correct, replace_count, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    rows,
                )

    def list_job_glossary_stats(self, job_id: str) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                """
                SELECT wrong, correct, replace_count
                FROM job_glossary_stats
                WHERE job_id=?
                ORDER BY replace_count DESC, wrong ASC
                """,
                (job_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def set_job_protocol(
        self,
        job_id: str,
        protocol_md: str,
        protocol_json: dict[str, Any],
        status: str = "generated",
    ) -> None:
        ts = now_utc()
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO job_protocols (
                    job_id, status, protocol_md, protocol_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status=excluded.status,
                    protocol_md=excluded.protocol_md,
                    protocol_json=excluded.protocol_json,
                    updated_at=excluded.updated_at
                """,
                (
                    job_id,
                    status,
                    protocol_md,
                    json.dumps(protocol_json, ensure_ascii=False),
                    ts,
                    ts,
                ),
            )

    def get_job_protocol(self, job_id: str) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM job_protocols WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                return None
            data = dict(row)
            raw = data.get("protocol_json")
            if raw:
                try:
                    data["protocol_json"] = json.loads(raw)
                except Exception:
                    data["protocol_json"] = None
            else:
                data["protocol_json"] = None
            return data

    def get_job(self, job_id: str, user_id: int | None = None, is_admin: bool = False) -> dict[str, Any] | None:
        with self.conn() as c:
            if is_admin or user_id is None:
                row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            else:
                row = c.execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
            return self._job_row_to_dict(row) if row else None

    def list_jobs(self, limit: int = 50, user_id: int | None = None, is_admin: bool = False) -> list[dict[str, Any]]:
        with self.conn() as c:
            if is_admin or user_id is None:
                rows = c.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            return [self._job_row_to_dict(r) for r in rows]

    def add_glossary_term(self, wrong: str, correct: str) -> dict[str, Any]:
        ts = now_utc()
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO glossary_terms (wrong, correct, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(wrong) DO UPDATE SET correct=excluded.correct
                """,
                (wrong.strip(), correct.strip(), ts),
            )
            row = c.execute(
                "SELECT id, wrong, correct, created_at FROM glossary_terms WHERE wrong=?",
                (wrong.strip(),),
            ).fetchone()
            return dict(row)

    def delete_glossary_term(self, term_id: int) -> bool:
        with self.conn() as c:
            cur = c.execute("DELETE FROM glossary_terms WHERE id=?", (term_id,))
            return cur.rowcount > 0

    def list_glossary_terms(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT id, wrong, correct, created_at FROM glossary_terms ORDER BY wrong ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def glossary_map(self) -> dict[str, str]:
        with self.conn() as c:
            rows = c.execute("SELECT wrong, correct FROM glossary_terms").fetchall()
            return {str(r[0]): str(r[1]) for r in rows}

    # Prompt library
    def list_prompts(self, profile: str | None = None, active_only: bool = False) -> list[dict[str, Any]]:
        with self.conn() as c:
            if profile and active_only:
                rows = c.execute(
                    "SELECT * FROM prompt_library WHERE profile=? AND is_active=1 ORDER BY is_default DESC, name ASC",
                    (profile,),
                ).fetchall()
            elif profile:
                rows = c.execute(
                    "SELECT * FROM prompt_library WHERE profile=? ORDER BY is_default DESC, name ASC",
                    (profile,),
                ).fetchall()
            elif active_only:
                rows = c.execute(
                    "SELECT * FROM prompt_library WHERE is_active=1 ORDER BY profile ASC, is_default DESC, name ASC"
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM prompt_library ORDER BY profile ASC, is_default DESC, name ASC"
                ).fetchall()
            return [self._prompt_row_to_dict(r) for r in rows]

    def get_prompt(self, prompt_id: int) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM prompt_library WHERE id=?", (prompt_id,)).fetchone()
            return self._prompt_row_to_dict(row) if row else None

    def get_default_prompt(self, profile: str) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute(
                """
                SELECT * FROM prompt_library
                WHERE profile=? AND is_active=1 AND is_default=1
                ORDER BY id ASC LIMIT 1
                """,
                (profile,),
            ).fetchone()
            if row:
                return self._prompt_row_to_dict(row)
            row = c.execute(
                """
                SELECT * FROM prompt_library
                WHERE profile=? AND is_active=1
                ORDER BY id ASC LIMIT 1
                """,
                (profile,),
            ).fetchone()
            return self._prompt_row_to_dict(row) if row else None

    def create_prompt(
        self,
        name: str,
        profile: str,
        system_prompt: str,
        user_template: str,
        is_default: bool = False,
        is_active: bool = True,
    ) -> dict[str, Any]:
        ts = now_utc()
        with self.conn() as c:
            cur = c.execute(
                """
                INSERT INTO prompt_library (
                    name, profile, system_prompt, user_template, is_default, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name.strip(),
                    profile.strip(),
                    system_prompt.strip(),
                    user_template.strip(),
                    1 if is_default else 0,
                    1 if is_active else 0,
                    ts,
                    ts,
                ),
            )
            prompt_id = cur.lastrowid
            if is_default:
                self._activate_prompt_in_conn(c, prompt_id)
            row = c.execute("SELECT * FROM prompt_library WHERE id=?", (prompt_id,)).fetchone()
            return self._prompt_row_to_dict(row)

    def update_prompt(
        self,
        prompt_id: int,
        name: str,
        profile: str,
        system_prompt: str,
        user_template: str,
        is_default: bool,
        is_active: bool,
    ) -> dict[str, Any] | None:
        ts = now_utc()
        with self.conn() as c:
            c.execute(
                """
                UPDATE prompt_library
                SET name=?, profile=?, system_prompt=?, user_template=?,
                    is_default=?, is_active=?, updated_at=?
                WHERE id=?
                """,
                (
                    name.strip(),
                    profile.strip(),
                    system_prompt.strip(),
                    user_template.strip(),
                    1 if is_default else 0,
                    1 if is_active else 0,
                    ts,
                    prompt_id,
                ),
            )
            if is_default:
                self._activate_prompt_in_conn(c, prompt_id)
            row = c.execute("SELECT * FROM prompt_library WHERE id=?", (prompt_id,)).fetchone()
            return self._prompt_row_to_dict(row) if row else None

    def delete_prompt(self, prompt_id: int) -> bool:
        with self.conn() as c:
            cur = c.execute("DELETE FROM prompt_library WHERE id=?", (prompt_id,))
            return cur.rowcount > 0

    def activate_prompt(self, prompt_id: int) -> dict[str, Any] | None:
        with self.conn() as c:
            self._activate_prompt_in_conn(c, prompt_id)
            row = c.execute("SELECT * FROM prompt_library WHERE id=?", (prompt_id,)).fetchone()
            return self._prompt_row_to_dict(row) if row else None

    def export_prompts_package(self, active_only: bool = False) -> dict[str, Any]:
        prompts = self.list_prompts(active_only=active_only)
        clean_items: list[dict[str, Any]] = []
        for p in prompts:
            clean_items.append(
                {
                    "name": p.get("name"),
                    "profile": p.get("profile"),
                    "system_prompt": p.get("system_prompt"),
                    "user_template": p.get("user_template"),
                    "is_default": bool(p.get("is_default")),
                    "is_active": bool(p.get("is_active")),
                }
            )
        return {
            "schema_version": 1,
            "version": "1.1",
            "format": "transcription_prompt_library",
            "exported_at": now_utc(),
            "active_only": bool(active_only),
            "prompts": clean_items,
        }

    def import_prompts(self, payload: dict[str, Any], mode: str = "merge") -> dict[str, Any]:
        if mode not in {"merge", "replace"}:
            raise ValueError("mode must be merge or replace")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")

        schema_version = payload.get("schema_version")
        if schema_version is None:
            raise ValueError("schema_version is required")
        try:
            schema_version_int = int(schema_version)
        except Exception:
            raise ValueError("schema_version must be an integer")
        if schema_version_int != 1:
            raise ValueError(f"unsupported schema_version: {schema_version_int}")

        prompts = payload.get("prompts")
        if not isinstance(prompts, list):
            raise ValueError("payload.prompts must be a list")

        created = 0
        updated = 0
        skipped = 0
        touched_profiles: set[str] = set()
        ts = now_utc()

        with self.conn() as c:
            if mode == "replace":
                c.execute("DELETE FROM prompt_library")

            for item in prompts:
                if not isinstance(item, dict):
                    skipped += 1
                    continue

                name = str(item.get("name") or "").strip()
                profile = str(item.get("profile") or "").strip().lower()
                system_prompt = str(item.get("system_prompt") or "").strip()
                user_template = str(item.get("user_template") or "").strip()
                is_default = 1 if bool(item.get("is_default")) else 0
                is_active = 1 if bool(item.get("is_active", True)) else 0

                if not name or profile not in {"voice", "meeting"} or not system_prompt or not user_template:
                    skipped += 1
                    continue

                touched_profiles.add(profile)
                row = c.execute("SELECT id FROM prompt_library WHERE name=?", (name,)).fetchone()
                if row:
                    c.execute(
                        """
                        UPDATE prompt_library
                        SET profile=?, system_prompt=?, user_template=?, is_default=?, is_active=?, updated_at=?
                        WHERE id=?
                        """,
                        (profile, system_prompt, user_template, is_default, is_active, ts, row[0]),
                    )
                    updated += 1
                else:
                    c.execute(
                        """
                        INSERT INTO prompt_library (
                            name, profile, system_prompt, user_template, is_default, is_active, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (name, profile, system_prompt, user_template, is_default, is_active, ts, ts),
                    )
                    created += 1

            self._normalize_defaults_in_conn(c, touched_profiles)

        return {
            "mode": mode,
            "schema_version": schema_version_int,
            "total_in_payload": len(prompts),
            "created": created,
            "updated": updated,
            "skipped": skipped,
        }

    @staticmethod
    def _activate_prompt_in_conn(c: sqlite3.Connection, prompt_id: int) -> None:
        row = c.execute("SELECT profile FROM prompt_library WHERE id=?", (prompt_id,)).fetchone()
        if not row:
            return
        profile = row[0]
        c.execute("UPDATE prompt_library SET is_default=0 WHERE profile=?", (profile,))
        c.execute("UPDATE prompt_library SET is_default=1 WHERE id=?", (prompt_id,))

    @staticmethod
    def _normalize_defaults_in_conn(c: sqlite3.Connection, profiles: set[str] | None = None) -> None:
        target_profiles = profiles or {"voice", "meeting"}
        for profile in target_profiles:
            active_rows = c.execute(
                "SELECT id, is_default FROM prompt_library WHERE profile=? AND is_active=1 ORDER BY id ASC",
                (profile,),
            ).fetchall()
            if not active_rows:
                c.execute("UPDATE prompt_library SET is_default=0 WHERE profile=?", (profile,))
                continue

            default_ids = [r[0] for r in active_rows if int(r[1]) == 1]
            chosen_default = default_ids[0] if default_ids else active_rows[0][0]
            c.execute("UPDATE prompt_library SET is_default=0 WHERE profile=?", (profile,))
            c.execute("UPDATE prompt_library SET is_default=1 WHERE id=?", (chosen_default,))

    @staticmethod
    def _job_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        if row is None:
            return {}
        data = dict(row)
        data["diarization"] = bool(data.get("diarization"))
        data["llm_enabled"] = bool(data.get("llm_enabled"))
        data["roles_enabled"] = bool(data.get("roles_enabled", 1))
        for field in ("raw_result_json", "speaker_map_json"):
            raw = data.get(field)
            if raw:
                try:
                    data[field] = json.loads(raw)
                except Exception:
                    data[field] = None
            else:
                data[field] = None
        return data

    @staticmethod
    def _prompt_row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        data = dict(row)
        data["is_default"] = bool(data.get("is_default"))
        data["is_active"] = bool(data.get("is_active"))
        return data
