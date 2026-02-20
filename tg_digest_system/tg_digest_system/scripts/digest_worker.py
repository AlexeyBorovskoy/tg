#!/usr/bin/env python3
"""
digest_worker.py — Главный воркер обработки каналов и генерации дайджестов.
Поддержка пошагового режима: --step=text|media|ocr|digest|all
"""

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
import pytz

from config import Config, Channel, load_config, get_enabled_channels
from config_db import merge_channels_from_sources
from delivery_settings import (
    load_delivery_settings,
    get_delivery_settings_for_channel,
    ChannelDeliverySettings,
)
import os
from database import Database
from telegram_client import TelegramService, TelegramBot
from ocr import OCRService  # Старый (для обратной совместимости)
from ocr_service_unified import UnifiedOCRService  # Новый (с облачными провайдерами)
from llm import LLMService
from rag import vec_schema_exists, index_digest_to_rag, index_consolidated_doc_to_rag
from gitlab_push import push_to_gitlab

logger = logging.getLogger(__name__)


def _log_ctx(channel: Optional[Channel] = None, step: str = "", msg_id: Optional[int] = None, **kw) -> dict:
    """Контекст для логов: channel, step, msg_id."""
    d = {}
    if channel is not None:
        d["channel_id"] = channel.id
        d["channel_name"] = channel.name
    if step:
        d["step"] = step
    if msg_id is not None:
        d["msg_id"] = msg_id
    d.update(kw)
    return d


class DigestWorker:
    """Воркер обработки каналов"""

    def __init__(self, config: Config):
        self.config = config
        self.db = Database(config)
        self.tg_service = TelegramService(config, self.db)
        self.tg_bot = TelegramBot(config)
        # Используем UnifiedOCRService если доступен, иначе старый OCRService
        if config.defaults.ocr_enabled:
            try:
                # Пробуем использовать новый унифицированный сервис
                ocr_provider = os.environ.get("OCR_PROVIDER", "tesseract").lower()
                # Поддерживаемые облачные провайдеры: ocr_space, easyocr, google_vision, yandex_vision
                cloud_providers = ("ocr_space", "easyocr", "google_vision", "yandex_vision")
                if ocr_provider in cloud_providers or hasattr(config.defaults, 'ocr_provider'):
                    self.ocr_service = UnifiedOCRService(config, self.db)
                    logger.info(f"Используется UnifiedOCRService (провайдер: {ocr_provider})")
                else:
                    # Fallback на старый Tesseract
                    self.ocr_service = OCRService(config, self.db)
                    logger.info("Используется OCRService (Tesseract)")
            except Exception as e:
                logger.warning(f"Не удалось инициализировать UnifiedOCRService: {e}, используем Tesseract")
                self.ocr_service = OCRService(config, self.db)
        else:
            self.ocr_service = None
        self.llm_service = LLMService(config)

    async def _get_notify_chat_id(self):
        """Получить chat_id для уведомлений (TG_STEP_NOTIFY_CHAT_ID или user id из Telethon)."""
        chat_id = getattr(self.config, "tg_step_notify_chat_id", None)
        if not chat_id:
            try:
                chat_id = await self.tg_service.get_me_user_id()
            except Exception as e:
                logger.debug("Step notify: не удалось получить chat_id из Telethon: %s", e)
        return chat_id

    async def _notify_error_global(self, message: str) -> None:
        """Отправляет уведомление об ошибке воркера (без привязки к каналу). Точный текст ошибки."""
        chat_id = await self._get_notify_chat_id()
        if not chat_id:
            return
        text = f"[TG Digest] Воркер не работает. Что произошло: {message}"
        try:
            await self.tg_bot.send_text(chat_id, text, parse_mode="")
        except Exception as e:
            logger.warning("Notify error send failed: %s", e)

    async def _notify_step(
        self,
        channel: Channel,
        step_name: str,
        success: bool,
        message: str,
        no_messages: bool = False,
        **extra: str,
    ) -> None:
        """Отправляет уведомление о шаге в Telegram.
        - Если no_messages=True или сообщение «Новых сообщений нет» — отправляется чёткое: «Новых сообщений нет. Канал: …»
        - Если success=False — отправляется точная ошибка: «Воркер не работает. Что произошло: …»
        """
        chat_id = await self._get_notify_chat_id()
        if not chat_id:
            return
        if no_messages or (success and message.strip() == "Новых сообщений нет."):
            text = f"[TG Digest] Новых сообщений нет. Канал: {channel.name}"
        elif not success:
            text = f"[TG Digest] Воркер не работает. Канал: {channel.name}, шаг {step_name}. Что произошло: {message}"
        else:
            text = f"[TG Digest] Канал {channel.name}, шаг {step_name}: {message}"
            for k, v in extra.items():
                if v:
                    text += f" {k}={v}"
        try:
            await self.tg_bot.send_text(chat_id, text, parse_mode="")
        except Exception as e:
            logger.warning("Step notify send failed: %s", e, extra=_log_ctx(channel=channel, step=step_name))

    async def process_channel(self, channel: Channel) -> Optional[int]:
        """Обрабатывает канал с учётом user_id (мультитенантность)"""
        # Получаем user_id из канала (если есть)
        user_id = getattr(channel, 'user_id', None)
        """
        Обрабатывает один канал: сбор сообщений, OCR, генерация дайджеста, рассылка.
        
        Returns:
            ID дайджеста или None
        """
        logger.info(f"=== Обработка канала: {channel.name} (ID: {channel.id}) ===")
        
        # 1. Получаем курсор (с учётом user_id)
        user_id = getattr(channel, 'user_id', None)
        last_msg_id = self.db.get_last_msg_id(channel.peer_type, channel.id, user_id)
        logger.info(f"Последний обработанный msg_id: {last_msg_id} (user_id={user_id})")
        
        # 2. Собираем новые сообщения
        new_messages = 0
        max_msg_id = last_msg_id
        
        try:
            async for message in self.tg_service.fetch_new_messages(channel, last_msg_id):
                # Сохраняем сообщение с user_id
                await self.tg_service.save_message(message, channel, user_id=user_id)
                
                # Сохраняем медиа для ВСЕХ сообщений с медиа (даже если сообщение уже есть в БД)
                if message.media and self.config.defaults.ocr_enabled:
                    # Проверяем, есть ли уже медиа для этого сообщения с правильным user_id
                    has_media = False
                    if user_id is not None:
                        with self.db.cursor() as cur:
                            cur.execute("""
                                SELECT 1 FROM tg.media 
                                WHERE peer_type = %s AND peer_id = %s AND msg_id = %s AND user_id = %s
                                LIMIT 1
                            """, (channel.peer_type, channel.id, message.id, user_id))
                            has_media = cur.fetchone() is not None
                    else:
                        has_media = self.db.has_media_for_message(channel.peer_type, channel.id, message.id)
                    
                    if not has_media:
                        await self.tg_service.save_media(message, channel, user_id=user_id)
                
                new_messages += 1
                max_msg_id = max(max_msg_id, message.id)
                
        except Exception as e:
            logger.error(
                "Ошибка сбора сообщений: %s",
                e,
                extra=_log_ctx(channel=channel, step="process_channel"),
            )
            logger.exception("process_channel fetch traceback")
            return None
        
        # Проверяем, нужно ли создать сводный документ при первом запуске (если файла нет)
        should_create_consolidated_doc = False
        if channel.consolidated_doc_path:
            doc_path = self.config.repo_dir / channel.consolidated_doc_path
            if not doc_path.exists():
                should_create_consolidated_doc = True
                logger.info(f"Сводный документ не существует, будет создан на основе всех сообщений и медиа")
        
        if new_messages == 0 and not should_create_consolidated_doc:
            logger.info("Новых сообщений нет")
            return None
        
        if new_messages > 0:
            logger.info(f"Собрано {new_messages} новых сообщений (до msg_id={max_msg_id})")
        
        # 3. OCR для всех изображений без OCR (с учетом user_id)
        if self.ocr_service:
            # Проверяем, асинхронный ли это сервис
            if hasattr(self.ocr_service, 'process_pending_media_async'):
                ocr_count = await self.ocr_service.process_pending_media_async(limit=50, user_id=user_id)
            else:
                # Старый синхронный метод
                ocr_count = self.ocr_service.process_pending_media(limit=50, user_id=user_id)
            logger.info(f"OCR обработано: {ocr_count} изображений")
        
        # 4. Генерируем RAW дайджест
        messages = self.db.get_messages_range(
            channel.peer_type, channel.id, last_msg_id, max_msg_id
        )
        raw_digest = self._format_raw_digest(channel, messages, last_msg_id, max_msg_id)
        
        # 5. Получаем OCR-тексты
        ocr_texts = self.db.get_ocr_text_for_range(
            channel.peer_type, channel.id, last_msg_id, max_msg_id
        )
        
        # 6. Генерируем LLM дайджест
        try:
            llm_digest, tokens_in, tokens_out = self.llm_service.generate_digest(
                channel, raw_digest, ocr_texts
            )
        except Exception as e:
            logger.error(
                "Ошибка LLM: %s",
                e,
                extra=_log_ctx(channel=channel, step="digest"),
            )
            logger.exception("LLM digest traceback")
            llm_digest = None
            tokens_in = tokens_out = 0
        
        # 7. Сохраняем дайджест в БД
        user_id = getattr(channel, 'user_id', None)
        digest_id = self.db.save_digest(
            peer_type=channel.peer_type,
            peer_id=channel.id,
            msg_id_from=last_msg_id,
            msg_id_to=max_msg_id,
            digest_raw=raw_digest,
            digest_llm=llm_digest,
            llm_model=self.config.openai_model if llm_digest else None,
            llm_tokens_in=tokens_in,
            llm_tokens_out=tokens_out,
            user_id=user_id,
        )
        
        # 8. Обновляем курсор
        self.db.update_last_msg_id(channel.peer_type, channel.id, max_msg_id, user_id=user_id)

        # 8b. Сводный инженерный документ: создается/обновляется на основе всех сообщений и медиа
        # Создается при первом запуске (если файла нет) или обновляется при наличии новых сообщений
        changes_summary = ""
        doc_path = self.config.repo_dir / channel.consolidated_doc_path if channel.consolidated_doc_path else None
        should_update_doc = channel.consolidated_doc_path and (new_messages > 0 or (doc_path and not doc_path.exists()))
        if should_update_doc:
            try:
                # Обновляем документ при наличии новых сообщений
                changes_summary = await self._update_consolidated_doc(channel)
                logger.info(f"Сводный документ {channel.name} обновлен на основе {new_messages} новых сообщений")
            except Exception as e:
                logger.error(
                    "Сводный документ %s: %s",
                    channel.name,
                    e,
                    extra=_log_ctx(channel=channel, step="consolidated_doc"),
                )
                logger.exception("consolidated_doc traceback")

        # 9. Рассылка получателям ТОЛЬКО если есть новые сообщения и создан дайджест
        # Не отправляем сообщения, если новых сообщений нет
        if llm_digest and new_messages > 0:
            await self._deliver_digest(
                channel, digest_id, llm_digest, last_msg_id, max_msg_id,
                changes_summary=changes_summary,
            )

        # 9b. Дайджест в файл для GitLab (дайджесты хранятся в БД и в репо)
        if llm_digest and self.config.gitlab_enabled:
            day_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            digest_dir = self.config.repo_dir / "docs" / "digests" / day_utc
            digest_dir.mkdir(parents=True, exist_ok=True)
            digest_filename = f"digest_llm_{channel.peer_type}_{channel.id}_from_{last_msg_id}_to_{max_msg_id}.md"
            digest_path = digest_dir / digest_filename
            full_digest = f"""# Дайджест: {channel.name}
Период: msg_id ({last_msg_id}, {max_msg_id}]
Сгенерировано: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}

{llm_digest}
"""
            digest_path.write_text(full_digest, encoding="utf-8")
            self._files_to_push.append(str(digest_path.relative_to(self.config.repo_dir)))
            logger.info("Дайджест записан в файл для GitLab: %s", digest_path)

        # 10. RAG: индексируем дайджест (если схема vec есть)
        if llm_digest and vec_schema_exists(self.db):
            try:
                index_digest_to_rag(
                    self.config, self.db,
                    channel.peer_type, channel.id, digest_id, llm_digest, user_id=user_id
                )
            except Exception as e:
                logger.warning(f"RAG index digest: {e}")
        
        logger.info(f"=== Канал {channel.name} обработан, digest_id={digest_id} ===")
        return digest_id
    
    async def process_channel_daily_summary(self, channel: Channel) -> Optional[int]:
        """
        Генерирует ежедневный сводный дайджест за день (даже если новых сообщений не было).
        Вызывается в 21:00 МСК.
        """
        logger.info(f"=== Ежедневный сводный дайджест: {channel.name} (ID: {channel.id}) ===")
        
        # Получаем диапазон дат для сегодняшнего дня (МСК)
        date_start_utc, date_end_utc = self._get_daily_date_range()
        msk_tz = pytz.timezone("Europe/Moscow")
        date_start_msk = date_start_utc.astimezone(msk_tz)
        
        # Получаем сообщения за день
        messages = self.db.get_messages_by_date(
            channel.peer_type, channel.id, date_start_utc, date_end_utc
        )
        
        # Получаем OCR-тексты за день
        ocr_texts = self.db.get_ocr_text_by_date(
            channel.peer_type, channel.id, date_start_utc, date_end_utc
        )
        
        # Определяем диапазон msg_id для сообщений за день
        msg_id_from = 0
        msg_id_to = 0
        if messages:
            msg_ids = [msg["msg_id"] for msg in messages]
            msg_id_from = min(msg_ids)
            msg_id_to = max(msg_ids)
        else:
            # Если сообщений нет, используем текущий курсор
            msg_id_from = self.db.get_last_msg_id(channel.peer_type, channel.id)
            msg_id_to = msg_id_from
        
        # Формируем RAW дайджест
        raw_digest = self._format_daily_raw_digest(
            channel, messages, date_start_utc, date_end_utc
        )
        
        # Генерируем LLM дайджест
        try:
            llm_digest, tokens_in, tokens_out = self.llm_service.generate_digest(
                channel, raw_digest, ocr_texts
            )
        except Exception as e:
            logger.error(
                "Ошибка LLM при генерации ежедневного дайджеста: %s",
                e,
                extra=_log_ctx(channel=channel, step="daily_summary"),
            )
            logger.exception("LLM daily summary traceback")
            llm_digest = None
            tokens_in = tokens_out = 0
        
        if not llm_digest:
            logger.warning("Не удалось сгенерировать ежедневный дайджест для %s", channel.name)
            return None
        
        # Сохраняем дайджест в БД
        user_id = getattr(channel, 'user_id', None)
        digest_id = self.db.save_digest(
            peer_type=channel.peer_type,
            peer_id=channel.id,
            msg_id_from=msg_id_from,
            msg_id_to=msg_id_to,
            digest_raw=raw_digest,
            digest_llm=llm_digest,
            llm_model=self.config.openai_model,
            llm_tokens_in=tokens_in,
            llm_tokens_out=tokens_out,
            user_id=user_id,
        )
        
        # Рассылаем дайджест получателям (даже если новых сообщений не было)
        await self._deliver_digest(
            channel, digest_id, llm_digest, msg_id_from, msg_id_to,
            changes_summary="",
        )
        
        # Сохраняем в файл для GitLab
        if self.config.gitlab_enabled:
            day_utc = date_start_msk.strftime("%Y-%m-%d")
            digest_dir = self.config.repo_dir / "docs" / "digests" / day_utc
            digest_dir.mkdir(parents=True, exist_ok=True)
            digest_filename = f"daily_digest_{channel.peer_type}_{channel.id}_{day_utc}.md"
            digest_path = digest_dir / digest_filename
            full_digest = f"""# Ежедневный дайджест: {channel.name}
Дата: {date_start_msk.strftime('%Y-%m-%d')} (МСК)
Период: {date_start_msk.strftime('%Y-%m-%d %H:%M:%S')} - {date_end_utc.astimezone(msk_tz).strftime('%Y-%m-%d %H:%M:%S')} МСК
Сгенерировано: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}
Сообщений за день: {len(messages)}

{llm_digest}
"""
            digest_path.write_text(full_digest, encoding="utf-8")
            self._files_to_push.append(str(digest_path.relative_to(self.config.repo_dir)))
            logger.info("Ежедневный дайджест записан в файл для GitLab: %s", digest_path)
        
        # RAG: индексируем дайджест
        user_id = getattr(channel, 'user_id', None)
        if vec_schema_exists(self.db):
            try:
                index_digest_to_rag(
                    self.config, self.db,
                    channel.peer_type, channel.id, digest_id, llm_digest, user_id=user_id
                )
            except Exception as e:
                logger.warning(f"RAG index daily digest: {e}")
        
        logger.info(f"=== Ежедневный дайджест для {channel.name} обработан, digest_id={digest_id} ===")
        return digest_id
    
    def _format_raw_digest(
        self, channel: Channel, messages: list[dict], msg_from: int, msg_to: int
    ) -> str:
        """Форматирует RAW дайджест"""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        lines = [
            f"# Increment digest",
            f"",
            f"Channel: {channel.name} (ID: {channel.id})",
            f"Window: msg_id ({msg_from}, {msg_to}]",
            f"Generated: {ts}",
            f"Messages: {len(messages)}",
            f"",
        ]
        
        for msg in messages:
            dt = msg["dt"].strftime("%Y-%m-%d %H:%M:%S") if msg["dt"] else "?"
            sender = msg.get("sender_name") or "[NO_SENDER]"
            text = (msg.get("text") or "[EMPTY]")[:1500].replace("\n", " ")
            lines.append(f"- **{dt}** `msg_id={msg['msg_id']}` **{sender}**: {text}")
        
        return "\n".join(lines)
    
    def _format_daily_raw_digest(
        self, channel: Channel, messages: list[dict], date_start: datetime, date_end: datetime
    ) -> str:
        """Форматирует RAW дайджест за день"""
        msk_tz = pytz.timezone("Europe/Moscow")
        date_start_msk = date_start.astimezone(msk_tz)
        date_end_msk = date_end.astimezone(msk_tz)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        lines = [
            f"# Daily digest",
            f"",
            f"Channel: {channel.name} (ID: {channel.id})",
            f"Period: {date_start_msk.strftime('%Y-%m-%d')} (Moscow time)",
            f"Generated: {ts}",
            f"Messages: {len(messages)}",
            f"",
        ]
        
        if len(messages) == 0:
            lines.append("**Новых сообщений за день не было.**")
        else:
            for msg in messages:
                dt = msg["dt"].strftime("%Y-%m-%d %H:%M:%S") if msg["dt"] else "?"
                sender = msg.get("sender_name") or "[NO_SENDER]"
                text = (msg.get("text") or "[EMPTY]")[:1500].replace("\n", " ")
                lines.append(f"- **{dt}** `msg_id={msg['msg_id']}` **{sender}**: {text}")
        
        return "\n".join(lines)
    
    def _is_daily_summary_time(self) -> bool:
        """Проверяет, наступило ли время для ежедневного сводного дайджеста (20:00 МСК)"""
        msk_tz = pytz.timezone("Europe/Moscow")
        now_msk = datetime.now(msk_tz)
        # Проверяем окно 21:00-21:05 МСК
        return now_msk.hour == 20 and now_msk.minute < 5
    
    def _get_daily_date_range(self) -> tuple[datetime, datetime]:
        """Возвращает диапазон дат для сегодняшнего дня (00:00-23:59:59 МСК)"""
        msk_tz = pytz.timezone("Europe/Moscow")
        now_msk = datetime.now(msk_tz)
        # Начало дня (00:00 МСК) - используем replace, сохраняя timezone
        date_start_msk = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
        # Конец дня (23:59:59 МСК)
        date_end_msk = now_msk.replace(hour=23, minute=59, second=59, microsecond=999999)
        # Конвертируем в UTC для запросов к БД (datetime уже имеет timezone)
        date_start_utc = date_start_msk.astimezone(timezone.utc)
        date_end_utc = date_end_msk.astimezone(timezone.utc)
        return date_start_utc, date_end_utc

    def _consolidated_update_marker_path(self, channel: Channel) -> Path:
        """Путь к файлу-маркеру последнего обновления сводного документа по каналу (раз в сутки)."""
        return self.config.repo_dir / f".last_consolidated_update_channel_{channel.id}"

    def _should_update_consolidated_doc_today(self, channel: Channel) -> bool:
        """Проверяет, нужно ли обновлять сводный документ сегодня (не чаще раза в сутки)."""
        marker = self._consolidated_update_marker_path(channel)
        if not marker.exists():
            return True
        try:
            last_date = marker.read_text(encoding="utf-8").strip()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            return last_date != today
        except Exception:
            return True

    def _mark_consolidated_doc_updated_today(self, channel: Channel) -> None:
        """Отмечает, что сводный документ по каналу обновлён сегодня."""
        marker = self._consolidated_update_marker_path(channel)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(datetime.now(timezone.utc).strftime("%Y-%m-%d"), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Не удалось записать маркер обновления сводного документа: {e}")

    def _build_consolidated_doc_link(self, channel: Channel) -> str:
        """Ссылка на сводный инженерный документ в GitLab (только при обновлении, раз в сутки)."""
        if not self.config.gitlab_enabled or not self.config.gitlab_repo_url or not channel.consolidated_doc_path:
            return ""
        # ssh://git@gitlab.ripas.ru:8611/analyzer/analysis-methodology.git -> https://gitlab.ripas.ru/analyzer/analysis-methodology/-/blob/<branch>/<path>
        url = (
            self.config.gitlab_repo_url.strip()
            .replace("ssh://git@", "https://")
            .replace("git@", "https://")
            .replace(":8611", "")
            .rstrip("/")
        )
        if url.endswith(".git"):
            url = url[:-4]
        branch = self.config.gitlab_branch or "main"
        path = channel.consolidated_doc_path.strip("/")
        return f"{url.rstrip('/')}/-/blob/{branch}/{path}"

    async def _update_consolidated_doc(self, channel: Channel) -> str:
        """
        Обновляет единый сводный инженерный документ по чату на основе всех сообщений и медиа.
        Возвращает краткое описание изменений для вставки в сообщение получателям.
        """
        # Ограничиваем объём для быстрого ответа API (~3–5 мин): последние N сообщений и OCR
        CONSOLIDATED_MSG_LIMIT = 500
        CONSOLIDATED_OCR_LIMIT = 200
        messages = self.db.get_messages_all_for_peer(channel.peer_type, channel.id, limit=CONSOLIDATED_MSG_LIMIT)
        ocr_texts = self.db.get_ocr_all_for_peer(channel.peer_type, channel.id, limit=CONSOLIDATED_OCR_LIMIT)
        # Дайджесты не используем - документ создается на основе сообщений и медиа
        recent_digests = []

        doc_path = self.config.repo_dir / channel.consolidated_doc_path
        previous_content = ""
        if doc_path.exists():
            try:
                previous_content = doc_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Не удалось прочитать предыдущий сводный документ: {e}")

        doc_content, changes_summary, _, _ = self.llm_service.generate_consolidated_doc(
            channel, messages, ocr_texts, recent_digests, previous_content
        )

        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(doc_content, encoding="utf-8")
        logger.info(f"Сводный документ записан: {doc_path}")
        if self.config.gitlab_enabled:
            self._files_to_push.append(channel.consolidated_doc_path)

        user_id = getattr(channel, 'user_id', None)
        if vec_schema_exists(self.db):
            try:
                index_consolidated_doc_to_rag(
                    self.config, self.db,
                    channel.peer_type, channel.id,
                    channel.consolidated_doc_path, doc_content, user_id=user_id
                )
            except Exception as e:
                logger.warning(f"RAG index consolidated_doc: {e}")

        return changes_summary or ""

    async def _deliver_digest(
        self,
        channel: Channel,
        digest_id: int,
        digest_text: str,
        msg_from: int,
        msg_to: int,
        changes_summary: str = "",
    ) -> None:
        """Рассылает дайджест получателям с учётом настроек доставки (БД для веб-каналов или config/digest_delivery.json)."""
        # Каналы из веба (web_channels) имеют атрибуты delivery_* из БД
        if getattr(channel, "delivery_importance", None) is not None:
            delivery = ChannelDeliverySettings(
                importance=channel.delivery_importance,
                send_file=getattr(channel, "delivery_send_file", True),
                send_text=getattr(channel, "delivery_send_text", True),
                text_max_chars=getattr(channel, "delivery_text_max_chars", None),
                summary_only=getattr(channel, "delivery_summary_only", False),
            )
        else:
            delivery = get_delivery_settings_for_channel(
                channel.id,
                getattr(self, "_delivery_settings_cache", None),
            )
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        file_name = f"digest_{channel.id}_{msg_from}_{msg_to}_{ts}.md"

        # Заголовок: для какого чата дайджест (для сводного бота)
        chat_header = (
            f"📊 *Дайджест по чату:* {channel.name}\n"
            f"Чат ID: `{channel.id}`\n\n"
        )
        # Ограничение длины текста по настройкам доставки (ознакомительные чаты)
        max_chars = delivery.text_max_chars
        if max_chars is not None and delivery.summary_only:
            short_text = (digest_text[:max_chars] + "…") if len(digest_text) > max_chars else digest_text
        else:
            short_text = digest_text[:3500] if len(digest_text) > 3500 else digest_text
        # Блок изменений и ссылка на инженерный документ — только при обновлении документа (раз в сутки)
        if changes_summary:
            doc_link = self._build_consolidated_doc_link(channel)
            short_text += (
                f"\n\n---\n_Изменения в сводном инженерном документе (чат: {channel.name}):_\n"
                f"{changes_summary}"
            )
            if doc_link:
                short_text += f"\n\n📄 [Инженерный документ]({doc_link})"
        message_text = chat_header + short_text

        # Полный текст дайджеста с заголовком (для файла): явно указан чат
        full_digest = f"""# Дайджест по чату: {channel.name}
Чат ID: {channel.id}
Период: msg_id ({msg_from}, {msg_to}]
Сгенерировано: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}

{digest_text}
"""
        if changes_summary:
            full_digest += f"\n---\n## Изменения в сводном инженерном документе (чат: {channel.name})\n\n{changes_summary}\n"
        file_data = full_digest.encode("utf-8")

        caption = f"Дайджест по чату: {channel.name} (ID: {channel.id})"
        
        user_id = getattr(channel, 'user_id', None)
        user_bot_token = getattr(channel, "user_bot_token", None) or None

        # Эффективные флаги: настройки по чату (digest_delivery.json) и получатель (recipient)
        do_send_text = delivery.send_text
        do_send_file = delivery.send_file

        for recipient in channel.recipients:
            if not recipient.telegram_id:
                logger.debug(f"Пропуск получателя {recipient.name}: telegram_id не задан")
                continue
            send_text = do_send_text and recipient.send_text
            send_file = do_send_file and recipient.send_file
            try:
                if send_text:
                    success = await self.tg_bot.send_text(
                        recipient.telegram_id,
                        message_text,
                        parse_mode="Markdown",
                        bot_token=user_bot_token,
                    )
                    self.db.save_delivery(
                        digest_id=digest_id,
                        telegram_id=recipient.telegram_id,
                        delivery_type="text",
                        status="sent" if success else "failed",
                        user_id=user_id,
                    )
                
                if send_file:
                    success = await self.tg_bot.send_document_bytes(
                        recipient.telegram_id,
                        file_data,
                        file_name,
                        caption=caption,
                        bot_token=user_bot_token,
                    )
                    self.db.save_delivery(
                        digest_id=digest_id,
                        telegram_id=recipient.telegram_id,
                        delivery_type="file",
                        status="sent" if success else "failed",
                        user_id=user_id,
                    )
                
                logger.info(
                    "Доставлено %s (ID: %s) [text=%s file=%s importance=%s]",
                    recipient.name, recipient.telegram_id, send_text, send_file, delivery.importance,
                )
                
            except Exception as e:
                logger.error(
                    "Ошибка доставки для %s: %s",
                    recipient.name,
                    e,
                    extra=_log_ctx(channel=channel, msg_id=digest_id),
                )
                logger.exception("deliver traceback")

    # -------------------------------------------------------------------------
    # Пошаговый режим (--step=text|media|ocr|digest)
    # -------------------------------------------------------------------------

    async def process_channel_step_text(self, channel: Channel) -> None:
        """Шаг 1: только текстовые сообщения, курсор, первый сводный документ (без медиа/OCR)."""
        step_name = "text"
        logger.info(
            "Step %s started",
            step_name,
            extra=_log_ctx(channel=channel, step=step_name),
        )
        try:
            last_msg_id = self.db.get_last_msg_id(channel.peer_type, channel.id)
            logger.info(
                "Step %s: last_msg_id=%s",
                step_name,
                last_msg_id,
                extra=_log_ctx(channel=channel, step=step_name),
            )
            new_messages = 0
            max_msg_id = last_msg_id
            last_message = None
            try:
                async for message in self.tg_service.fetch_new_messages(channel, last_msg_id):
                    last_message = message
                    await self.tg_service.save_message(message, channel)
                    new_messages += 1
                    max_msg_id = max(max_msg_id, message.id)
                    logger.debug(
                        "Step %s: msg_id=%s saved",
                        step_name,
                        message.id,
                        extra=_log_ctx(channel=channel, step=step_name, msg_id=message.id),
                    )
            except Exception as e:
                logger.exception(
                    "Step %s: fetch/save FAIL msg_id=%s: %s",
                    step_name,
                    getattr(last_message, "id", "?"),
                    e,
                    extra=_log_ctx(channel=channel, step=step_name),
                )
                await self._notify_step(
                    channel,
                    step_name,
                    success=False,
                    message=f"Ошибка сбора: {e}",
                )
                return

            if new_messages == 0:
                logger.info(
                    "Step %s: новых сообщений нет",
                    step_name,
                    extra=_log_ctx(channel=channel, step=step_name),
                )
                await self._notify_step(channel, step_name, success=True, message="Новых сообщений нет.", no_messages=True)
                return

            self.db.update_last_msg_id(channel.peer_type, channel.id, max_msg_id, user_id=user_id)
            logger.info(
                "Step %s: собрано %s сообщений, курсор обновлён до %s",
                step_name,
                new_messages,
                max_msg_id,
                extra=_log_ctx(channel=channel, step=step_name),
            )

            # Сводный документ на основе всех сообщений и медиа (независимо от дайджестов)
            if channel.consolidated_doc_path:
                try:
                    # Ограничиваем объём для быстрого ответа API (~3–5 мин)
                    messages = self.db.get_messages_all_for_peer(channel.peer_type, channel.id, limit=500)
                    ocr_texts = self.db.get_ocr_all_for_peer(channel.peer_type, channel.id, limit=200)
                    # Дайджесты не используем - документ создается на основе сообщений и медиа
                    recent_digests = []
                    previous_content = ""
                    doc_path = self.config.repo_dir / channel.consolidated_doc_path
                    if doc_path.exists():
                        try:
                            previous_content = doc_path.read_text(encoding="utf-8")
                        except Exception as e:
                            logger.warning("Не удалось прочитать предыдущий сводный документ: %s", e)
                    doc_content, changes_summary, _, _ = self.llm_service.generate_consolidated_doc(
                        channel, messages, ocr_texts, recent_digests, previous_content
                    )
                    doc_path.parent.mkdir(parents=True, exist_ok=True)
                    doc_path.write_text(doc_content, encoding="utf-8")
                    logger.info(
                        "Step %s: сводный документ записан на основе %s сообщений и %s OCR текстов: %s",
                        step_name,
                        len(messages),
                        len(ocr_texts),
                        doc_path,
                        extra=_log_ctx(channel=channel, step=step_name),
                    )
                    if self.config.gitlab_enabled:
                        self._files_to_push.append(channel.consolidated_doc_path)
                except Exception as e:
                    logger.exception(
                        "Step %s: сводный документ FAIL: %s",
                        step_name,
                        e,
                        extra=_log_ctx(channel=channel, step=step_name),
                    )
                    await self._notify_step(
                        channel,
                        step_name,
                        success=False,
                        message=f"Сводный документ: {e}",
                    )
                    return

            await self._notify_step(
                channel,
                step_name,
                success=True,
                message=f"Сообщений: {new_messages}, курсор: {max_msg_id}.",
                doc_path=channel.consolidated_doc_path or "",
            )
            logger.info(
                "Step %s finished: total=%s cursor=%s",
                step_name,
                new_messages,
                max_msg_id,
                extra=_log_ctx(channel=channel, step=step_name),
            )
        except Exception as e:
            logger.exception(
                "Step %s FAIL: %s",
                step_name,
                e,
                extra=_log_ctx(channel=channel, step=step_name),
            )
            await self._notify_step(channel, step_name, success=False, message=str(e))

    async def process_channel_step_media(self, channel: Channel) -> None:
        """Шаг 2: загрузка медиа в БД для ВСЕХ сообщений с медиа (даже если сообщения уже есть в БД)."""
        step_name = "media"
        logger.info(
            "Step %s started",
            step_name,
            extra=_log_ctx(channel=channel, step=step_name),
        )
        try:
            user_id = getattr(channel, 'user_id', None)
            total = 0
            failed = 0
            
            await self.tg_service.connect()
            entity = await self.tg_service._client.get_entity(channel.id)
            
            # Обрабатываем ВСЕ сообщения с медиа (от старых к новым)
            try:
                async for message in self.tg_service._client.iter_messages(entity, reverse=True, limit=None):
                    if not message.media:
                        continue
                    
                    # Проверяем, есть ли уже медиа для этого сообщения с правильным user_id
                    has_media = False
                    if user_id is not None:
                        # Проверяем с учетом user_id
                        with self.db.cursor() as cur:
                            cur.execute("""
                                SELECT 1 FROM tg.media 
                                WHERE peer_type = %s AND peer_id = %s AND msg_id = %s AND user_id = %s
                                LIMIT 1
                            """, (channel.peer_type, channel.id, message.id, user_id))
                            has_media = cur.fetchone() is not None
                    else:
                        has_media = self.db.has_media_for_message(channel.peer_type, channel.id, message.id)
                    
                    if has_media:
                        logger.debug(
                            "Step %s: msg_id=%s уже есть медиа (user_id=%s), пропуск",
                            step_name, message.id, user_id,
                            extra=_log_ctx(channel=channel, step=step_name, msg_id=message.id),
                        )
                        continue
                    
                    try:
                        media_id = await self.tg_service.save_media(message, channel, user_id=user_id)
                        if media_id:
                            total += 1
                        logger.debug(
                            "Step %s: msg_id=%s media_id=%s OK",
                            step_name,
                            message.id,
                            media_id,
                            extra=_log_ctx(channel=channel, step=step_name, msg_id=message.id),
                        )
                    except Exception as e:
                        failed += 1
                        logger.warning(
                            "Step %s: msg_id=%s FAIL: %s",
                            step_name,
                            message.id,
                            e,
                            extra=_log_ctx(channel=channel, step=step_name, msg_id=message.id),
                        )
                        await self._notify_step(
                            channel,
                            step_name,
                            success=False,
                            message=f"msg_id={message.id} FAIL: {e}",
                        )
            except Exception as e:
                logger.exception(
                    "Step %s: fetch FAIL: %s",
                    step_name,
                    e,
                    extra=_log_ctx(channel=channel, step=step_name),
                )
                await self._notify_step(channel, step_name, success=False, message=f"Ошибка итерации: {e}")
                return

            await self._notify_step(
                channel,
                step_name,
                success=True,
                message=f"Загружено: {total}, ошибок: {failed}.",
            )
            logger.info(
                "Step %s finished: total=%s failed=%s",
                step_name,
                total,
                failed,
                extra=_log_ctx(channel=channel, step=step_name),
            )
        except Exception as e:
            logger.exception(
                "Step %s FAIL: %s",
                step_name,
                e,
                extra=_log_ctx(channel=channel, step=step_name),
            )
            await self._notify_step(channel, step_name, success=False, message=str(e))

    async def process_channel_step_ocr(self, channel: Channel) -> None:
        """Шаг 3: OCR по одному медиа."""
        step_name = "ocr"
        logger.info(
            "Step %s started",
            step_name,
            extra=_log_ctx(channel=channel, step=step_name),
        )
        if not self.ocr_service:
            logger.warning("Step %s: OCR отключён в конфиге", step_name)
            await self._notify_step(channel, step_name, success=True, message="OCR отключён.")
            return
        try:
            user_id = getattr(channel, 'user_id', None)
            processed = 0
            failed = 0
            while True:
                media_list = self.db.get_media_without_ocr(limit=1, user_id=user_id)
                if not media_list:
                    break
                m = media_list[0]
                media_id = m["id"]
                msg_id = m["msg_id"]
                peer_type = m["peer_type"]
                peer_id = m["peer_id"]
                media_user_id = m.get("user_id") or user_id
                try:
                    file_data = m.get("file_data")
                    if file_data is not None:
                        file_data = bytes(file_data)
                    elif m.get("local_path"):
                        file_data = Path(m["local_path"]).read_bytes()
                    else:
                        logger.warning("Step %s: media_id=%s нет данных", step_name, media_id)
                        failed += 1
                        continue
                    # Проверяем, асинхронный ли это метод
                    if asyncio.iscoroutinefunction(self.ocr_service.process_image):
                        text, metadata = await self.ocr_service.process_image(file_data)
                        ocr_model = metadata.get('provider', 'unknown')
                    else:
                        # Старый синхронный метод
                        text, _ = self.ocr_service.process_image(file_data)
                        ocr_model = "tesseract"
                    
                    self.db.save_ocr_text(
                        media_id=media_id,
                        peer_type=peer_type,
                        peer_id=peer_id,
                        msg_id=msg_id,
                        ocr_text=text or "",
                        ocr_model=ocr_model,
                        user_id=media_user_id,
                    )
                    processed += 1
                    logger.debug(
                        "Step %s: media_id=%s msg_id=%s OK",
                        step_name,
                        media_id,
                        msg_id,
                        extra=_log_ctx(channel=channel, step=step_name, msg_id=msg_id),
                    )
                except Exception as e:
                    failed += 1
                    logger.warning(
                        "Step %s: media_id=%s msg_id=%s FAIL: %s",
                        step_name,
                        media_id,
                        msg_id,
                        e,
                        extra=_log_ctx(channel=channel, step=step_name, msg_id=msg_id),
                    )
                    await self._notify_step(
                        channel,
                        step_name,
                        success=False,
                        message=f"media_id={media_id} FAIL: {e}",
                    )

            await self._notify_step(
                channel,
                step_name,
                success=True,
                message=f"Обработано: {processed}, ошибок: {failed}.",
            )
            logger.info(
                "Step %s finished: processed=%s failed=%s",
                step_name,
                processed,
                failed,
                extra=_log_ctx(channel=channel, step=step_name),
            )
        except Exception as e:
            logger.exception(
                "Step %s FAIL: %s",
                step_name,
                e,
                extra=_log_ctx(channel=channel, step=step_name),
            )
            await self._notify_step(channel, step_name, success=False, message=str(e))

    async def process_channel_step_digest(self, channel: Channel) -> Optional[int]:
        """Шаг 4: только дайджест и сводный документ (данные уже в БД)."""
        step_name = "digest"
        logger.info(
            "Step %s started",
            step_name,
            extra=_log_ctx(channel=channel, step=step_name),
        )
        try:
            last_msg_id = self.db.get_last_msg_id(channel.peer_type, channel.id)
            max_msg_id = self.db.get_max_msg_id(channel.peer_type, channel.id)
            if max_msg_id <= last_msg_id:
                logger.info(
                    "Step %s: новых сообщений нет (last=%s max=%s)",
                    step_name,
                    last_msg_id,
                    max_msg_id,
                    extra=_log_ctx(channel=channel, step=step_name),
                )
                await self._notify_step(channel, step_name, success=True, message="Новых сообщений нет.", no_messages=True)
                return None
            messages = self.db.get_messages_range(
                channel.peer_type, channel.id, last_msg_id, max_msg_id
            )
            user_id = getattr(channel, "user_id", None)
            new_messages = len(messages)
            raw_digest = self._format_raw_digest(channel, messages, last_msg_id, max_msg_id)
            ocr_texts = self.db.get_ocr_text_for_range(
                channel.peer_type, channel.id, last_msg_id, max_msg_id
            )
            try:
                llm_digest, tokens_in, tokens_out = self.llm_service.generate_digest(
                    channel, raw_digest, ocr_texts
                )
            except Exception as e:
                logger.exception("Step %s: LLM FAIL: %s", step_name, e, extra=_log_ctx(channel=channel, step=step_name))
                await self._notify_step(channel, step_name, success=False, message=f"LLM: {e}")
                return None
            digest_id = self.db.save_digest(
                peer_type=channel.peer_type,
                peer_id=channel.id,
                msg_id_from=last_msg_id,
                msg_id_to=max_msg_id,
                digest_raw=raw_digest,
                digest_llm=llm_digest,
                llm_model=self.config.openai_model,
                llm_tokens_in=tokens_in,
                llm_tokens_out=tokens_out,
            )
            self.db.update_last_msg_id(channel.peer_type, channel.id, max_msg_id, user_id=user_id)
            changes_summary = ""
            # Сводный документ создается/обновляется на основе всех сообщений и медиа при наличии новых данных
            if channel.consolidated_doc_path and new_messages > 0:
                try:
                    changes_summary = await self._update_consolidated_doc(channel)
                    logger.info(f"Сводный документ {channel.name} обновлен на основе {new_messages} новых сообщений")
                except Exception as e:
                    logger.exception("Step %s: consolidated_doc FAIL: %s", step_name, e)
            if llm_digest:
                await self._deliver_digest(
                    channel, digest_id, llm_digest, last_msg_id, max_msg_id,
                    changes_summary=changes_summary,
                )
            if llm_digest and self.config.gitlab_enabled:
                day_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                digest_dir = self.config.repo_dir / "docs" / "digests" / day_utc
                digest_dir.mkdir(parents=True, exist_ok=True)
                digest_filename = f"digest_llm_{channel.peer_type}_{channel.id}_from_{last_msg_id}_to_{max_msg_id}.md"
                digest_path = digest_dir / digest_filename
                full_digest = f"""# Дайджест: {channel.name}\nПериод: msg_id ({last_msg_id}, {max_msg_id}]\n\n{llm_digest}\n"""
                digest_path.write_text(full_digest, encoding="utf-8")
                self._files_to_push.append(str(digest_path.relative_to(self.config.repo_dir)))
            await self._notify_step(
                channel,
                step_name,
                success=True,
                message=f"digest_id={digest_id} msg_id={last_msg_id}-{max_msg_id}.",
            )
            logger.info(
                "Step %s finished: digest_id=%s",
                step_name,
                digest_id,
                extra=_log_ctx(channel=channel, step=step_name),
            )
            return digest_id
        except Exception as e:
            logger.exception(
                "Step %s FAIL: %s",
                step_name,
                e,
                extra=_log_ctx(channel=channel, step=step_name),
            )
            await self._notify_step(channel, step_name, success=False, message=str(e))
            return None

    async def run_once(self, step: Optional[str] = None) -> None:
        """Один цикл обработки всех каналов. step: text|media|ocr|digest|all (None = all)."""
        self._files_to_push = []
        # Кэш настроек доставки на цикл (config/digest_delivery.json)
        self._delivery_settings_cache = load_delivery_settings()
        # Загружаем каналы из БД и файла (мультитенантность)
        merged_channels = merge_channels_from_sources(self.config)
        self.config.channels = merged_channels
        channels = get_enabled_channels(self.config)
        step_mode = (step or "all").lower()
        logger.info(
            "Запуск обработки %s каналов, step=%s",
            len(channels),
            step_mode,
            extra={"step": step_mode},
        )

        for channel in channels:
            try:
                if step_mode == "text":
                    await self.process_channel_step_text(channel)
                elif step_mode == "media":
                    await self.process_channel_step_media(channel)
                elif step_mode == "ocr":
                    await self.process_channel_step_ocr(channel)
                elif step_mode == "digest":
                    await self.process_channel_step_digest(channel)
                else:
                    await self.process_channel(channel)
            except Exception as e:
                logger.error(
                    "Ошибка обработки %s: %s",
                    channel.name,
                    e,
                    extra=_log_ctx(channel=channel, step=step_mode),
                )
                logger.exception("run_once traceback")

            await asyncio.sleep(2)

        # Сводный документ обновляется после каждого дайджеста внутри process_channel (см. шаг 8b)

        # Пуш в GitLab (gitlab.ripas.ru): дайджесты и сводные документы
        if self._files_to_push and self.config.gitlab_enabled:
            try:
                msg = "digests and docs " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                ok = push_to_gitlab(
                    self.config.repo_dir,
                    self._files_to_push,
                    msg,
                    branch=self.config.gitlab_branch,
                    ssh_key_path=self.config.gitlab_ssh_key or "",
                )
                if not ok:
                    logger.warning("GitLab push не выполнен")
            except Exception as e:
                logger.error("GitLab push: %s", e)
        
        await self.tg_service.disconnect()

        # Heartbeat для мониторинга: последний успешный цикл (healthcheck проверяет возраст файла)
        try:
            heartbeat_dir = Path(self.config.logs_dir)
            heartbeat_dir.mkdir(parents=True, exist_ok=True)
            (heartbeat_dir / "heartbeat.txt").write_text(
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Heartbeat не записан: {e}")
    
    async def run_loop(self, interval_minutes: int = 30) -> None:
        """Запуск в режиме бесконечного цикла"""
        logger.info(f"Запуск воркера в режиме цикла (интервал: {interval_minutes} мин)")
        
        # Отслеживаем последний день, когда был сгенерирован ежедневный дайджест
        last_daily_summary_date = None
        config_file = Path(self.config.repo_dir) / "config" / "channels.json"
        if not config_file.exists():
            config_file = Path(os.environ.get("CONFIG_FILE", str(config_file)))
        
        while True:
            try:
                # Перезагружаем конфигурацию перед каждым циклом (для поддержки динамического добавления каналов)
                try:
                    new_config = load_config(str(config_file))
                    # Обновляем только список каналов, остальные настройки не меняем
                    old_channel_ids = {ch.id for ch in self.config.channels}
                    new_channel_ids = {ch.id for ch in new_config.channels}
                    
                    if old_channel_ids != new_channel_ids:
                        logger.info(f"Обнаружены изменения в конфигурации каналов. Было: {len(old_channel_ids)}, стало: {len(new_channel_ids)}")
                        # Пересоздаём воркер с новой конфигурацией
                        self.config = new_config
                        self.db = Database(self.config)
                        self.tg_service = TelegramService(self.config, self.db)
                        # Переинициализируем OCR и LLM сервисы
                        if self.config.defaults.ocr_enabled:
                            try:
                                ocr_provider = os.environ.get("OCR_PROVIDER", "tesseract").lower()
                                cloud_providers = ("ocr_space", "easyocr", "google_vision", "yandex_vision")
                                if ocr_provider in cloud_providers or hasattr(self.config.defaults, 'ocr_provider'):
                                    self.ocr_service = UnifiedOCRService(self.config, self.db)
                                else:
                                    self.ocr_service = OCRService(self.config, self.db)
                            except Exception as e:
                                logger.warning(f"Не удалось переинициализировать OCR: {e}")
                                self.ocr_service = OCRService(self.config, self.db)
                        else:
                            self.ocr_service = None
                        self.llm_service = LLMService(self.config)
                        logger.info("Конфигурация перезагружена, новые каналы будут обработаны")
                except Exception as e:
                    logger.warning(f"Ошибка перезагрузки конфигурации: {e}, используем текущую")
                
                # Проверяем, наступило ли время для ежедневного сводного дайджеста (20:00 МСК)
                msk_tz = pytz.timezone("Europe/Moscow")
                now_msk = datetime.now(msk_tz)
                today_date = now_msk.date()
                
                if self._is_daily_summary_time() and last_daily_summary_date != today_date:
                    logger.info("Время для ежедневного сводного дайджеста (20:00 МСК)")
                    channels = get_enabled_channels(self.config)
                    for channel in channels:
                        try:
                            await self.process_channel_daily_summary(channel)
                        except Exception as e:
                            logger.error(
                                "Ошибка генерации ежедневного дайджеста для %s: %s",
                                channel.name,
                                e,
                                extra=_log_ctx(channel=channel, step="daily_summary"),
                            )
                            logger.exception("daily_summary traceback")
                        await asyncio.sleep(2)
                    
                    last_daily_summary_date = today_date
                    logger.info("Ежедневные сводные дайджесты сгенерированы")
                
                # Обычный цикл обработки каналов
                await self.run_once()
            except Exception as e:
                logger.error(f"Ошибка в цикле: {e}")
                try:
                    await self._notify_error_global(str(e))
                except Exception as notify_err:
                    logger.warning("Не удалось отправить уведомление об ошибке: %s", notify_err)
            
            logger.info(f"Ожидание {interval_minutes} минут...")
            await asyncio.sleep(interval_minutes * 60)


async def main():
    """Точка входа"""
    import argparse
    
    parser = argparse.ArgumentParser(description="TG Digest Worker")
    parser.add_argument("--config", default=None, help="Путь к channels.json")
    parser.add_argument("--once", action="store_true", help="Однократный запуск")
    parser.add_argument("--interval", type=int, default=30, help="Интервал в минутах")
    parser.add_argument("--channel", type=int, help="Обработать только указанный канал")
    parser.add_argument(
        "--step",
        choices=("text", "media", "ocr", "digest", "all"),
        default="all",
        help="Шаг пайплайна: text (только сообщения+документ), media (загрузка медиа), ocr, digest, all (полный цикл)",
    )
    parser.add_argument("--debug", action="store_true", help="Debug режим")
    args = parser.parse_args()
    
    # Логирование
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    # Загружаем конфиг
    config = load_config(args.config)
    
    # Создаём воркер
    worker = DigestWorker(config)
    
    if args.channel:
        channel = next((c for c in config.channels if c.id == args.channel), None)
        if channel:
            step = args.step if args.step != "all" else None
            if step == "text":
                await worker.process_channel_step_text(channel)
            elif step == "media":
                await worker.process_channel_step_media(channel)
            elif step == "ocr":
                await worker.process_channel_step_ocr(channel)
            elif step == "digest":
                await worker.process_channel_step_digest(channel)
            else:
                await worker.process_channel(channel)
        else:
            logger.error("Канал %s не найден", args.channel)
    elif args.once:
        step = args.step if args.step != "all" else None
        await worker.run_once(step=step)
    else:
        await worker.run_loop(args.interval)


if __name__ == "__main__":
    asyncio.run(main())
