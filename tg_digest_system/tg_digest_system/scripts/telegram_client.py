#!/usr/bin/env python3
"""
telegram_client.py — Работа с Telegram API через Telethon
"""

import logging
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, AsyncIterator

from telethon import TelegramClient
from telethon.tl.types import (
    Message,
    MessageMediaPhoto,
    MessageMediaDocument,
    User,
    Channel,
)

from config import Config, Channel as ChannelConfig
from database import Database

logger = logging.getLogger(__name__)


class TelegramService:
    """Сервис для работы с Telegram"""
    
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self._client: Optional[TelegramClient] = None
        self._clients: dict[str, TelegramClient] = {}
    
    def _resolve_channel_credentials(self, channel: Optional[ChannelConfig]) -> tuple[Optional[int], int, str, str]:
        """
        Выбирает credentials для канала:
        1) пользовательские (если есть в channel.user_tg_*)
        2) системные из env/config
        """
        user_id = getattr(channel, "user_id", None) if channel is not None else None
        api_id = getattr(channel, "user_tg_api_id", None) if channel is not None else None
        api_hash = getattr(channel, "user_tg_api_hash", None) if channel is not None else None
        session_file = getattr(channel, "user_tg_session_file", None) if channel is not None else None

        resolved_api_id = int(api_id) if api_id else self.config.tg_api_id
        resolved_api_hash = (api_hash or self.config.tg_api_hash or "").strip()
        resolved_session_file = (session_file or self.config.tg_session_file or "").strip()
        return user_id, resolved_api_id, resolved_api_hash, resolved_session_file

    async def connect(
        self,
        *,
        user_id: Optional[int] = None,
        api_id: Optional[int] = None,
        api_hash: Optional[str] = None,
        session_file: Optional[str] = None,
    ) -> TelegramClient:
        """Подключается к Telegram и возвращает клиент (общий или пользовательский)."""
        resolved_api_id = int(api_id) if api_id else self.config.tg_api_id
        resolved_api_hash = (api_hash or self.config.tg_api_hash or "").strip()
        resolved_session_file = (session_file or self.config.tg_session_file or "").strip()

        if not resolved_api_id or not resolved_api_hash or not resolved_session_file:
            raise ValueError("Telegram credentials не настроены: api_id/api_hash/session_file")

        session_path = Path(resolved_session_file)
        session_path.parent.mkdir(parents=True, exist_ok=True)

        client_key = f"{user_id or 0}:{session_path}"
        existing = self._clients.get(client_key)
        if existing is not None and existing.is_connected():
            self._client = existing
            return existing

        client = TelegramClient(
            str(session_path),
            resolved_api_id,
            resolved_api_hash,
        )
        await client.start()
        me = await client.get_me()
        logger.info(
            "Подключено к Telegram как: %s (ID: %s), user_id=%s",
            me.first_name,
            me.id,
            user_id,
        )
        self._clients[client_key] = client
        self._client = client
        return client

    async def get_me_user_id(self) -> Optional[int]:
        """Возвращает user id аккаунта Telethon (для уведомлений в «свой» чат)."""
        client = await self.connect()
        try:
            me = await client.get_me()
            return me.id if me else None
        except Exception as e:
            logger.warning("get_me_user_id: %s", e)
            return None

    async def disconnect(self) -> None:
        """Отключается от Telegram"""
        for key, client in list(self._clients.items()):
            try:
                if client.is_connected():
                    await client.disconnect()
                    logger.debug("Отключено от Telegram (%s)", key)
            except Exception:
                logger.exception("Ошибка отключения Telegram клиента %s", key)
        self._clients.clear()
        self._client = None
    
    async def fetch_new_messages(
        self,
        channel: ChannelConfig,
        last_msg_id: int,
    ) -> AsyncIterator[Message]:
        """
        Получает новые сообщения из канала.
        
        Args:
            channel: Конфигурация канала
            last_msg_id: Последний обработанный msg_id
        
        Yields:
            Message: Сообщения Telegram
        """
        user_id, api_id, api_hash, session_file = self._resolve_channel_credentials(channel)
        client = await self.connect(
            user_id=user_id,
            api_id=api_id,
            api_hash=api_hash,
            session_file=session_file,
        )
        
        try:
            entity = await client.get_entity(channel.id)
            logger.info(f"Получаем сообщения из {channel.name} (ID: {channel.id}) после msg_id={last_msg_id}")
            
            # Получаем сообщения
            # Используем iter_messages без reverse, чтобы получить все сообщения
            # Telethon автоматически обрабатывает пагинацию
            count = 0
            async for message in client.iter_messages(
                entity,
                min_id=last_msg_id,
                reverse=False,  # От новых к старым (получаем все)
                limit=None,  # Без ограничений - получаем все сообщения
            ):
                if message.id > last_msg_id:
                    count += 1
                    yield message
            
            logger.info(f"Получено {count} сообщений из {channel.name}")
                    
        except Exception as e:
            logger.error(f"Ошибка получения сообщений из {channel.name}: {e}")
            raise
    
    async def save_message(self, message: Message, channel: ChannelConfig, user_id: Optional[int] = None) -> None:
        """Сохраняет сообщение в БД"""
        # Определяем отправителя
        sender_id = None
        sender_name = None
        
        if message.sender:
            sender_id = message.sender.id
            if isinstance(message.sender, User):
                sender_name = " ".join(filter(None, [
                    message.sender.first_name,
                    message.sender.last_name,
                ]))
            elif isinstance(message.sender, Channel):
                sender_name = message.sender.title
        
        # Время сообщения
        dt = message.date
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        # Сохраняем сообщение с user_id
        self.db.upsert_message(
            peer_type=channel.peer_type,
            peer_id=channel.id,
            msg_id=message.id,
            dt=dt,
            sender_id=sender_id,
            sender_name=sender_name,
            text=message.text or "",
            raw_json=message.to_dict() if self.config.debug else None,
            user_id=user_id,
        )
        
        logger.debug(f"Сохранено сообщение {message.id} от {sender_name}")
    
    async def save_media(self, message: Message, channel: ChannelConfig, user_id: Optional[int] = None) -> Optional[int]:
        """
        Сохраняет медиафайл из сообщения.
        
        Args:
            message: Сообщение с медиа
            channel: Конфигурация канала
            user_id: ID пользователя (опционально)
        
        Returns:
            ID медиафайла в БД или None
        """
        if not message.media:
            return None
        
        # Определяем тип медиа
        media_type = self._detect_media_type(message)
        if media_type == "other":
            return None
        
        channel_user_id, api_id, api_hash, session_file = self._resolve_channel_credentials(channel)
        client = await self.connect(
            user_id=channel_user_id,
            api_id=api_id,
            api_hash=api_hash,
            session_file=session_file,
        )
        
        try:
            # Оптимизация: сохраняем медиа на диск вместо загрузки в память
            # Это предотвращает OOM при большом количестве медиафайлов
            media_dir = self.config.media_dir / f"{channel.peer_type}_{channel.id}"
            media_dir.mkdir(parents=True, exist_ok=True)
            
            # Временный файл для загрузки
            temp_file = media_dir / f"temp_{message.id}"
            
            # Скачиваем напрямую в файл
            await client.download_media(message, file=str(temp_file))
            
            if not temp_file.exists() or temp_file.stat().st_size == 0:
                temp_file.unlink(missing_ok=True)
                return None
            
            # Читаем файл для SHA256 и сохранения
            file_data = temp_file.read_bytes()
            
            # Определяем имя файла
            file_name = f"{message.id}"
            if hasattr(message.media, "document") and message.media.document:
                for attr in message.media.document.attributes:
                    if hasattr(attr, "file_name") and attr.file_name:
                        file_name = f"{message.id}_{attr.file_name}"
                        break
            
            # Расширение по типу
            if media_type == "photo":
                file_name += ".jpg"
            
            # MIME-тип
            mime_type = None
            if hasattr(message, "file") and message.file:
                mime_type = message.file.mime_type
            
            # SHA256
            sha256 = hashlib.sha256(file_data).hexdigest()
            
            # Определяем финальный путь файла
            file_path = media_dir / file_name
            
            # Переименовываем временный файл в финальный
            if temp_file.exists():
                temp_file.rename(file_path)
            
            # Сохраняем в БД (используем local_path для экономии памяти)
            media_id = self.db.upsert_media(
                peer_type=channel.peer_type,
                peer_id=channel.id,
                msg_id=message.id,
                media_type=media_type,
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=len(file_data),
                sha256=sha256,
                local_path=str(file_path),
                file_data=None,  # Не сохраняем в БД для экономии памяти
                user_id=user_id,
            )
            
            logger.debug(f"Сохранено медиа {file_name} для msg_id={message.id} (user_id={user_id})")
            return media_id
            
        except Exception as e:
            logger.error(f"Ошибка сохранения медиа для msg_id={message.id}: {e}")
            logger.exception("save_media traceback")
            return None
    
    def _detect_media_type(self, message: Message) -> str:
        """Определяет тип медиа"""
        if isinstance(message.media, MessageMediaPhoto):
            return "photo"
        if isinstance(message.media, MessageMediaDocument):
            doc = message.media.document
            if doc:
                mime = doc.mime_type or ""
                if mime.startswith("video/"):
                    return "video"
                if mime.startswith("audio/"):
                    return "voice"
                if "sticker" in mime or any(
                    hasattr(a, "stickerset") for a in (doc.attributes or [])
                ):
                    return "sticker"
            return "file"
        return "other"


class TelegramBot:
    """Бот для отправки дайджестов"""
    
    def __init__(self, config: Config):
        self.config = config
        self.bot_token = config.tg_bot_token
    
    def _base_url(self, bot_token: Optional[str] = None) -> str:
        token = (bot_token or self.bot_token or "").strip()
        return f"https://api.telegram.org/bot{token}"
    
    async def send_text(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "Markdown",
        bot_token: Optional[str] = None,
    ) -> bool:
        """Отправляет текстовое сообщение"""
        import aiohttp
        
        # Ограничение Telegram: 4096 символов
        if len(text) > 4096:
            text = text[:4090] + "\n..."
        
        token = (bot_token or self.bot_token or "").strip()
        if not token:
            logger.error("Не задан bot token для отправки текста")
            return False
        
        url = f"{self._base_url(token)}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=30) as resp:
                    result = await resp.json()
                    if result.get("ok"):
                        logger.debug(f"Сообщение отправлено в {chat_id}")
                        return True
                    else:
                        logger.error(f"Ошибка отправки в {chat_id}: {result}")
                        return False
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения в {chat_id}: {e}")
            return False
    
    async def send_document(
        self,
        chat_id: int,
        file_path: Path,
        caption: Optional[str] = None,
        bot_token: Optional[str] = None,
    ) -> bool:
        """Отправляет документ"""
        import aiohttp
        
        token = (bot_token or self.bot_token or "").strip()
        if not token:
            logger.error("Не задан bot token для отправки документа")
            return False
        
        url = f"{self._base_url(token)}/sendDocument"
        
        try:
            async with aiohttp.ClientSession() as session:
                data = aiohttp.FormData()
                data.add_field("chat_id", str(chat_id))
                data.add_field(
                    "document",
                    open(file_path, "rb"),
                    filename=file_path.name,
                )
                if caption:
                    data.add_field("caption", caption[:1024])  # Лимит подписи
                
                async with session.post(url, data=data, timeout=60) as resp:
                    result = await resp.json()
                    if result.get("ok"):
                        logger.debug(f"Документ отправлен в {chat_id}")
                        return True
                    else:
                        logger.error(f"Ошибка отправки документа в {chat_id}: {result}")
                        return False
        except Exception as e:
            logger.error(f"Ошибка отправки документа в {chat_id}: {e}")
            return False
    
    async def send_document_bytes(
        self,
        chat_id: int,
        file_data: bytes,
        file_name: str,
        caption: Optional[str] = None,
        bot_token: Optional[str] = None,
    ) -> bool:
        """Отправляет документ из байтов"""
        import aiohttp
        from io import BytesIO
        
        token = (bot_token or self.bot_token or "").strip()
        if not token:
            logger.error("Не задан bot token для отправки документа (bytes)")
            return False
        
        url = f"{self._base_url(token)}/sendDocument"
        
        try:
            async with aiohttp.ClientSession() as session:
                data = aiohttp.FormData()
                data.add_field("chat_id", str(chat_id))
                data.add_field(
                    "document",
                    BytesIO(file_data),
                    filename=file_name,
                )
                if caption:
                    data.add_field("caption", caption[:1024])
                
                async with session.post(url, data=data, timeout=60) as resp:
                    result = await resp.json()
                    if result.get("ok"):
                        logger.debug(f"Документ {file_name} отправлен в {chat_id}")
                        return True
                    else:
                        logger.error(f"Ошибка отправки документа в {chat_id}: {result}")
                        return False
        except Exception as e:
            logger.error(f"Ошибка отправки документа в {chat_id}: {e}")
            return False
