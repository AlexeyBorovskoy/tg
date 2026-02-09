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
    
    async def connect(self) -> None:
        """Подключается к Telegram"""
        if self._client is not None and self._client.is_connected():
            return
        
        session_path = Path(self.config.tg_session_file)
        session_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._client = TelegramClient(
            str(session_path),
            self.config.tg_api_id,
            self.config.tg_api_hash,
        )
        
        await self._client.start()
        me = await self._client.get_me()
        logger.info(f"Подключено к Telegram как: {me.first_name} (ID: {me.id})")

    async def get_me_user_id(self) -> Optional[int]:
        """Возвращает user id аккаунта Telethon (для уведомлений в «свой» чат)."""
        await self.connect()
        if self._client is None:
            return None
        try:
            me = await self._client.get_me()
            return me.id if me else None
        except Exception as e:
            logger.warning("get_me_user_id: %s", e)
            return None

    async def disconnect(self) -> None:
        """Отключается от Telegram"""
        if self._client:
            await self._client.disconnect()
            logger.debug("Отключено от Telegram")
    
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
        await self.connect()
        
        try:
            entity = await self._client.get_entity(channel.id)
            logger.info(f"Получаем сообщения из {channel.name} (ID: {channel.id}) после msg_id={last_msg_id}")
            
            # Получаем сообщения
            # Используем iter_messages без reverse, чтобы получить все сообщения
            # Telethon автоматически обрабатывает пагинацию
            count = 0
            async for message in self._client.iter_messages(
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
        logger.info(f"save_media вызван для msg_id={message.id}, user_id={user_id}")
        if not message.media:
            logger.debug(f"msg_id={message.id} не имеет медиа")
            return None
        
        # Определяем тип медиа
        media_type = self._detect_media_type(message)
        logger.info(f"msg_id={message.id}, media_type={media_type}")
        if media_type == "other":
            logger.debug(f"msg_id={message.id}, media_type=other, пропуск")
            return None
        
        await self.connect()
        
        try:
            # Оптимизация: сохраняем медиа на диск вместо загрузки в память
            # Это предотвращает OOM при большом количестве медиафайлов
            media_dir = self.config.media_dir / f"{channel.peer_type}_{channel.id}"
            media_dir.mkdir(parents=True, exist_ok=True)
            
            # Временный файл для загрузки
            temp_file = media_dir / f"temp_{message.id}"
            
            # Скачиваем напрямую в файл
            logger.debug(f"Скачивание медиа для msg_id={message.id} в {temp_file}")
            await self._client.download_media(message, file=str(temp_file))
            
            if not temp_file.exists() or temp_file.stat().st_size == 0:
                logger.warning(f"Медиа для msg_id={message.id} не скачалось или пустое")
                temp_file.unlink(missing_ok=True)
                return None
            
            logger.debug(f"Медиа для msg_id={message.id} скачано, размер: {temp_file.stat().st_size} байт")
            
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
                # Если финальный файл уже существует, удаляем его перед переименованием
                if file_path.exists():
                    logger.debug(f"Файл {file_path} уже существует, удаляем перед переименованием")
                    file_path.unlink()
                logger.debug(f"Переименование {temp_file} -> {file_path}")
                temp_file.rename(file_path)
            else:
                logger.warning(f"Временный файл {temp_file} не существует после скачивания")
            
            # Сохраняем в БД (используем local_path для экономии памяти)
            logger.debug(f"Сохранение медиа в БД для msg_id={message.id}, file_name={file_name}, user_id={user_id}")
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
            
            logger.info(f"Сохранено медиа {file_name} для msg_id={message.id} (user_id={user_id}), media_id={media_id}")
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
        self._base_url = f"https://api.telegram.org/bot{self.bot_token}"
    
    async def send_text(self, chat_id: int, text: str, parse_mode: str = "Markdown") -> bool:
        """Отправляет текстовое сообщение"""
        import aiohttp
        
        # Ограничение Telegram: 4096 символов
        if len(text) > 4096:
            text = text[:4090] + "\n..."
        
        url = f"{self._base_url}/sendMessage"
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
    ) -> bool:
        """Отправляет документ"""
        import aiohttp
        
        url = f"{self._base_url}/sendDocument"
        
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
    ) -> bool:
        """Отправляет документ из байтов"""
        import aiohttp
        from io import BytesIO
        
        url = f"{self._base_url}/sendDocument"
        
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
