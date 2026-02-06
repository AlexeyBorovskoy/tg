#!/usr/bin/env python3
"""
database.py — Работа с PostgreSQL
"""

import logging
from datetime import datetime
from typing import Optional
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor, Json

from config import Config

logger = logging.getLogger(__name__)


class Database:
    """Класс для работы с PostgreSQL"""
    
    def __init__(self, config: Config):
        self.config = config
        self._conn = None
    
    def connect(self) -> None:
        """Устанавливает соединение с БД"""
        if self._conn is not None and not self._conn.closed:
            return
        
        self._conn = psycopg2.connect(
            host=self.config.pg_host,
            port=self.config.pg_port,
            dbname=self.config.pg_database,
            user=self.config.pg_user,
            password=self.config.pg_password,
        )
        self._conn.autocommit = False
        logger.info(f"Подключено к PostgreSQL: {self.config.pg_host}:{self.config.pg_port}/{self.config.pg_database}")
    
    def close(self) -> None:
        """Закрывает соединение"""
        if self._conn and not self._conn.closed:
            self._conn.close()
            logger.debug("Соединение с PostgreSQL закрыто")
    
    @contextmanager
    def cursor(self, dict_cursor: bool = False):
        """Контекстный менеджер для курсора"""
        self.connect()
        cursor_factory = RealDictCursor if dict_cursor else None
        cur = self._conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cur
            self._conn.commit()
        except Exception as e:
            self._conn.rollback()
            logger.error(f"Ошибка БД: {e}")
            raise
        finally:
            cur.close()
    
    # =========================================================================
    # Сообщения
    # =========================================================================
    
    def upsert_message(
        self,
        peer_type: str,
        peer_id: int,
        msg_id: int,
        dt: datetime,
        sender_id: Optional[int],
        sender_name: Optional[str],
        text: Optional[str],
        raw_json: Optional[dict] = None,
        user_id: Optional[int] = None,
    ) -> None:
        """Сохраняет или обновляет сообщение"""
        with self.cursor() as cur:
            # Если user_id не передан, пытаемся получить из БД по peer_id (для обратной совместимости)
            if user_id is None:
                cur.execute("""
                    SELECT DISTINCT user_id FROM tg.messages 
                    WHERE peer_type = %s AND peer_id = %s AND user_id IS NOT NULL
                    LIMIT 1
                """, (peer_type, peer_id))
                row = cur.fetchone()
                if row:
                    user_id = row[0]
                else:
                    # Если не найдено, используем user_id=1 (основной пользователь)
                    user_id = 1
            
            cur.execute("""
                INSERT INTO tg.messages (peer_type, peer_id, msg_id, dt, sender_id, sender_name, text, raw_json, user_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (peer_type, peer_id, msg_id)
                DO UPDATE SET
                    dt = EXCLUDED.dt,
                    sender_id = EXCLUDED.sender_id,
                    sender_name = EXCLUDED.sender_name,
                    text = EXCLUDED.text,
                    raw_json = EXCLUDED.raw_json,
                    user_id = COALESCE(EXCLUDED.user_id, tg.messages.user_id)
            """, (peer_type, peer_id, msg_id, dt, sender_id, sender_name, text, Json(raw_json) if raw_json else None, user_id))
    
    def get_messages_range(
        self,
        peer_type: str,
        peer_id: int,
        msg_id_from: int,
        msg_id_to: int,
        user_id: Optional[int] = None,
    ) -> list[dict]:
        """Получает сообщения в диапазоне msg_id"""
        with self.cursor(dict_cursor=True) as cur:
            if user_id:
                cur.execute("""
                    SELECT msg_id, dt, sender_name, text
                    FROM tg.messages
                    WHERE peer_type = %s AND peer_id = %s AND user_id = %s
                      AND msg_id > %s AND msg_id <= %s
                    ORDER BY dt ASC, msg_id ASC
                """, (peer_type, peer_id, user_id, msg_id_from, msg_id_to))
            else:
                cur.execute("""
                    SELECT msg_id, dt, sender_name, text
                    FROM tg.messages
                    WHERE peer_type = %s AND peer_id = %s
                      AND msg_id > %s AND msg_id <= %s
                    ORDER BY dt ASC, msg_id ASC
                """, (peer_type, peer_id, msg_id_from, msg_id_to))
            return cur.fetchall()
    
    def get_max_msg_id(self, peer_type: str, peer_id: int) -> int:
        """Получает максимальный msg_id для канала"""
        with self.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(MAX(msg_id), 0)
                FROM tg.messages
                WHERE peer_type = %s AND peer_id = %s
            """, (peer_type, peer_id))
            result = cur.fetchone()
            return result[0] if result else 0
    
    # =========================================================================
    # Медиа
    # =========================================================================
    
    def upsert_media(
        self,
        peer_type: str,
        peer_id: int,
        msg_id: int,
        media_type: str,
        file_name: str,
        mime_type: Optional[str],
        size_bytes: Optional[int],
        sha256: Optional[str],
        file_data: Optional[bytes] = None,
        local_path: Optional[str] = None,
    ) -> int:
        """Сохраняет медиафайл, возвращает ID"""
        with self.cursor() as cur:
            cur.execute("""
                INSERT INTO tg.media (peer_type, peer_id, msg_id, media_type, file_name, mime_type, size_bytes, sha256, file_data, local_path)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (peer_type, peer_id, msg_id, file_name)
                DO UPDATE SET
                    mime_type = EXCLUDED.mime_type,
                    size_bytes = EXCLUDED.size_bytes,
                    sha256 = EXCLUDED.sha256,
                    file_data = EXCLUDED.file_data,
                    local_path = EXCLUDED.local_path
                RETURNING id
            """, (peer_type, peer_id, msg_id, media_type, file_name, mime_type, size_bytes, sha256, 
                  psycopg2.Binary(file_data) if file_data else None, local_path))
            return cur.fetchone()[0]
    
    def has_media_for_message(self, peer_type: str, peer_id: int, msg_id: int) -> bool:
        """Проверяет, есть ли в БД медиа для сообщения"""
        with self.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM tg.media
                WHERE peer_type = %s AND peer_id = %s AND msg_id = %s
                LIMIT 1
            """, (peer_type, peer_id, msg_id))
            return cur.fetchone() is not None

    def get_media_without_ocr(self, limit: int = 10) -> list[dict]:
        """Получает медиафайлы без OCR-текста"""
        with self.cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT m.id, m.peer_type, m.peer_id, m.msg_id, m.file_name, m.file_data, m.local_path
                FROM tg.media m
                LEFT JOIN tg.media_text mt ON m.id = mt.media_id
                WHERE m.media_type = 'photo'
                  AND mt.id IS NULL
                  AND (m.file_data IS NOT NULL OR m.local_path IS NOT NULL)
                ORDER BY m.created_at DESC
                LIMIT %s
            """, (limit,))
            return cur.fetchall()
    
    def save_ocr_text(
        self,
        media_id: int,
        peer_type: str,
        peer_id: int,
        msg_id: int,
        ocr_text: str,
        ocr_model: str = "tesseract",
        ocr_confidence: Optional[float] = None,
    ) -> None:
        """Сохраняет OCR-текст"""
        with self.cursor() as cur:
            # Проверяем существование записи
            cur.execute("SELECT id FROM tg.media_text WHERE media_id = %s", (media_id,))
            existing = cur.fetchone()
            
            if existing:
                # Обновляем существующую запись
                cur.execute("""
                    UPDATE tg.media_text
                    SET ocr_text = %s, ocr_model = %s, ocr_confidence = %s, updated_at = now()
                    WHERE media_id = %s
                """, (ocr_text, ocr_model, ocr_confidence, media_id))
            else:
                # Создаем новую запись
                cur.execute("""
                    INSERT INTO tg.media_text (media_id, peer_type, peer_id, msg_id, ocr_text, ocr_model, ocr_confidence)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (media_id, peer_type, peer_id, msg_id, ocr_text, ocr_model, ocr_confidence))
    
    def get_ocr_by_image_hash(self, image_hash: str) -> Optional[str]:
        """
        Получает OCR-текст по хэшу изображения (дедупликация).
        
        Args:
            image_hash: SHA256 хэш изображения
        
        Returns:
            OCR-текст или None если не найден
        """
        with self.cursor() as cur:
            # Используем sha256 из tg.media для поиска
            cur.execute("""
                SELECT mt.ocr_text
                FROM tg.media_text mt
                JOIN tg.media m ON mt.media_id = m.id
                WHERE m.sha256 = %s
                  AND mt.ocr_text IS NOT NULL AND mt.ocr_text != ''
                LIMIT 1
            """, (image_hash,))
            row = cur.fetchone()
            return row[0] if row else None
    
    def get_ocr_text_for_range(
        self,
        peer_type: str,
        peer_id: int,
        msg_id_from: int,
        msg_id_to: int,
    ) -> list[dict]:
        """Получает OCR-текст для диапазона сообщений"""
        with self.cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT msg_id, ocr_text
                FROM tg.media_text
                WHERE peer_type = %s AND peer_id = %s
                  AND msg_id > %s AND msg_id <= %s
                  AND ocr_text IS NOT NULL AND ocr_text != ''
                ORDER BY msg_id
            """, (peer_type, peer_id, msg_id_from, msg_id_to))
            return cur.fetchall()

    def get_messages_all_for_peer(
        self,
        peer_type: str,
        peer_id: int,
        limit: int = 2000,
    ) -> list[dict]:
        """Получает сообщения по чату для контекста сводного документа (последние limit по msg_id)"""
        with self.cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT id, msg_id, dt, sender_name, text
                FROM tg.messages
                WHERE peer_type = %s AND peer_id = %s
                ORDER BY msg_id DESC
                LIMIT %s
            """, (peer_type, peer_id, limit))
            rows = cur.fetchall()
        return list(reversed(rows))

    def get_ocr_all_for_peer(
        self,
        peer_type: str,
        peer_id: int,
        limit: int = 500,
    ) -> list[dict]:
        """Получает OCR-текст по чату для контекста сводного документа"""
        with self.cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT msg_id, ocr_text
                FROM tg.media_text
                WHERE peer_type = %s AND peer_id = %s
                  AND ocr_text IS NOT NULL AND ocr_text != ''
                ORDER BY msg_id DESC
                LIMIT %s
            """, (peer_type, peer_id, limit))
            return list(reversed(cur.fetchall()))

    def get_recent_digests_for_peer(
        self,
        peer_type: str,
        peer_id: int,
        limit: int = 20,
    ) -> list[dict]:
        """Получает последние дайджесты по чату (digest_llm) для контекста сводного документа"""
        with self.cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT id, msg_id_from, msg_id_to, digest_llm, created_at
                FROM rpt.digests
                WHERE peer_type = %s AND peer_id = %s AND digest_llm IS NOT NULL
                ORDER BY created_at DESC
                LIMIT %s
            """, (peer_type, peer_id, limit))
            return list(reversed(cur.fetchall()))

    def get_messages_by_date(
        self,
        peer_type: str,
        peer_id: int,
        date_start: datetime,
        date_end: datetime,
    ) -> list[dict]:
        """Получает сообщения за указанный период (по дате dt)"""
        with self.cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT msg_id, dt, sender_name, text
                FROM tg.messages
                WHERE peer_type = %s AND peer_id = %s
                  AND dt >= %s AND dt < %s
                ORDER BY dt ASC, msg_id ASC
            """, (peer_type, peer_id, date_start, date_end))
            return cur.fetchall()
    
    def get_ocr_text_by_date(
        self,
        peer_type: str,
        peer_id: int,
        date_start: datetime,
        date_end: datetime,
    ) -> list[dict]:
        """Получает OCR-текст для сообщений за указанный период (по дате сообщения)"""
        with self.cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT mt.msg_id, mt.ocr_text
                FROM tg.media_text mt
                JOIN tg.messages m ON mt.peer_type = m.peer_type 
                    AND mt.peer_id = m.peer_id 
                    AND mt.msg_id = m.msg_id
                WHERE mt.peer_type = %s AND mt.peer_id = %s
                  AND m.dt >= %s AND m.dt < %s
                  AND mt.ocr_text IS NOT NULL AND mt.ocr_text != ''
                ORDER BY mt.msg_id
            """, (peer_type, peer_id, date_start, date_end))
            return cur.fetchall()
    
    # =========================================================================
    # Состояние отчётов
    # =========================================================================
    
    def get_last_msg_id(self, peer_type: str, peer_id: int, user_id: Optional[int] = None) -> int:
        """Получает последний обработанный msg_id"""
        with self.cursor() as cur:
            if user_id:
                cur.execute("""
                    SELECT last_msg_id FROM rpt.report_state
                    WHERE peer_type = %s AND peer_id = %s AND user_id = %s
                """, (peer_type, peer_id, user_id))
            else:
                cur.execute("""
                    SELECT last_msg_id FROM rpt.report_state
                    WHERE peer_type = %s AND peer_id = %s
                """, (peer_type, peer_id))
            result = cur.fetchone()
            return result[0] if result else 0
    
    def update_last_msg_id(self, peer_type: str, peer_id: int, last_msg_id: int, user_id: Optional[int] = None) -> None:
        """Обновляет курсор обработки"""
        with self.cursor() as cur:
            if user_id:
                cur.execute("""
                    INSERT INTO rpt.report_state (peer_type, peer_id, last_msg_id, user_id, updated_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (user_id, peer_type, peer_id)
                    DO UPDATE SET last_msg_id = EXCLUDED.last_msg_id, updated_at = now()
                """, (peer_type, peer_id, last_msg_id, user_id))
            else:
                cur.execute("""
                INSERT INTO rpt.report_state (peer_type, peer_id, last_msg_id, last_poll_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (peer_type, peer_id)
                DO UPDATE SET
                    last_msg_id = EXCLUDED.last_msg_id,
                    last_poll_at = now(),
                    updated_at = now()
            """, (peer_type, peer_id, last_msg_id))
    
    # =========================================================================
    # Дайджесты
    # =========================================================================
    
    def save_digest(
        self,
        peer_type: str,
        peer_id: int,
        msg_id_from: int,
        msg_id_to: int,
        digest_raw: str,
        digest_llm: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_tokens_in: Optional[int] = None,
        llm_tokens_out: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """Сохраняет дайджест, возвращает ID"""
        with self.cursor() as cur:
            # Если user_id не передан, пытаемся получить из БД
            if user_id is None:
                cur.execute("""
                    SELECT DISTINCT user_id FROM tg.messages 
                    WHERE peer_type = %s AND peer_id = %s AND user_id IS NOT NULL
                    LIMIT 1
                """, (peer_type, peer_id))
                row = cur.fetchone()
                user_id = row[0] if row else 1
            
            cur.execute("""
                INSERT INTO rpt.digests (peer_type, peer_id, msg_id_from, msg_id_to, digest_raw, digest_llm, llm_model, llm_tokens_in, llm_tokens_out, user_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (peer_type, peer_id, msg_id_from, msg_id_to, digest_raw, digest_llm, llm_model, llm_tokens_in, llm_tokens_out, user_id))
            return cur.fetchone()[0]
    
    def save_delivery(
        self,
        digest_id: int,
        telegram_id: int,
        delivery_type: str,
        status: str,
        error_message: Optional[str] = None,
        recipient_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> None:
        """Сохраняет запись о доставке"""
        with self.cursor() as cur:
            # Если user_id не передан, получаем из дайджеста
            if user_id is None:
                cur.execute("SELECT user_id FROM rpt.digests WHERE id = %s", (digest_id,))
                row = cur.fetchone()
                user_id = row[0] if row else 1
            
            cur.execute("""
                INSERT INTO rpt.deliveries (digest_id, recipient_id, telegram_id, delivery_type, status, error_message, user_id, sent_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, CASE WHEN %s = 'sent' THEN now() ELSE NULL END)
            """, (digest_id, recipient_id, telegram_id, delivery_type, status, error_message, user_id, status))


# Глобальный экземпляр (опционально)
_db: Optional[Database] = None


def get_database(config: Config) -> Database:
    """Получает или создаёт экземпляр Database"""
    global _db
    if _db is None:
        _db = Database(config)
    return _db
