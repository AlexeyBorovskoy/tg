#!/usr/bin/env python3
"""
add_channel.py — Автоматическое добавление нового чата в систему мониторинга

Использование:
    python add_channel.py <chat_id> [--name "Название"] [--prompt prompts/digest_management.md] [--recipient-id 123456789]

Что делает:
    1. Определяет тип чата (channel/group/chat)
    2. Загружает ВСЕ исторические сообщения
    3. Обрабатывает OCR для всех медиафайлов
    4. Создаёт сводный инженерный документ
    5. Добавляет канал в channels.json
    6. Уведомляет о завершении
"""

import asyncio
import json
import logging
import sys
import argparse
from pathlib import Path
from typing import Optional

import sys
from pathlib import Path

# Добавляем путь к скриптам в PYTHONPATH
script_dir = Path(__file__).parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

from config import Config, load_config, Channel, Recipient
from database import Database
from telegram_client import TelegramService
from ocr_service_unified import UnifiedOCRService
from llm import LLMService
from rag import vec_schema_exists, index_consolidated_doc_to_rag
from digest_worker import DigestWorker

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


async def detect_chat_type(tg_service: TelegramService, chat_id: int) -> tuple[str, str]:
    """
    Определяет тип чата и его название.
    
    Returns:
        (peer_type, name)
    """
    await tg_service.connect()
    client = tg_service._client
    
    try:
        entity = await client.get_entity(chat_id)
        
        if hasattr(entity, 'megagroup') and entity.megagroup:
            peer_type = "group"
        elif hasattr(entity, 'broadcast') and entity.broadcast:
            peer_type = "channel"
        else:
            peer_type = "group"
        
        name = getattr(entity, 'title', None) or getattr(entity, 'first_name', 'Unknown')
        
        return peer_type, name
    except Exception as e:
        logger.error(f"Ошибка определения типа чата {chat_id}: {e}")
        raise


async def load_full_history(
    tg_service: TelegramService,
    channel: Channel,
    db: Database,
    ocr_service: Optional[UnifiedOCRService],
) -> tuple[int, int]:
    """
    Загружает ВСЮ историю чата в БД.
    
    Returns:
        (total_messages, total_media)
    """
    logger.info(f"Начинаем загрузку полной истории чата {channel.name} (ID: {channel.id})...")
    
    await tg_service.connect()
    client = tg_service._client
    
    entity = await client.get_entity(channel.id)
    
    total_messages = 0
    total_media = 0
    
    # Загружаем все сообщения (от самых старых к новым)
    async for message in client.iter_messages(entity, reverse=True, limit=None):
        # Сохраняем сообщение
        await tg_service.save_message(message, channel)
        total_messages += 1
        
        # Сохраняем медиа и обрабатываем OCR
        if message.media and ocr_service:
            try:
                media_id = await tg_service.save_media(message, channel)
                if media_id:
                    total_media += 1
                    # Медиа сохранено, OCR обработается позже через process_pending_media_async
            except Exception as e:
                logger.warning(f"Ошибка обработки медиа msg_id={message.id}: {e}")
        
        # Прогресс каждые 100 сообщений
        if total_messages % 100 == 0:
            logger.info(f"Загружено {total_messages} сообщений, {total_media} медиа...")
    
    logger.info(f"Загрузка завершена: {total_messages} сообщений, {total_media} медиа")
    return total_messages, total_media


async def create_consolidated_doc(
    worker: DigestWorker,
    channel: Channel,
    db: Database,
) -> str:
    """Создаёт сводный инженерный документ на основе всех сообщений."""
    logger.info(f"Создание сводного документа для {channel.name}...")
    
    try:
        changes_summary = await worker._update_consolidated_doc(channel)
        logger.info(f"Сводный документ создан: {channel.consolidated_doc_path}")
        return changes_summary
    except Exception as e:
        logger.error(f"Ошибка создания сводного документа: {e}")
        raise


def add_channel_to_config(
    config_path: Path,
    channel: Channel,
    default_prompt: str = "prompts/digest_management.md",
) -> None:
    """Добавляет канал в channels.json."""
    logger.info(f"Добавление канала в конфигурацию: {config_path}")
    
    # Читаем текущую конфигурацию
    with open(config_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Проверяем, нет ли уже такого канала
    for ch in data.get('channels', []):
        if ch['id'] == channel.id:
            logger.warning(f"Канал {channel.id} уже существует в конфигурации!")
            return
    
    # Формируем имя файла для сводного документа
    doc_name = channel.name.lower().replace(' ', '_').replace('/', '_')
    doc_name = ''.join(c for c in doc_name if c.isalnum() or c in '_-')
    consolidated_doc_path = f"docs/reference/{doc_name}.md"
    
    # Создаём запись канала
    channel_data = {
        "id": channel.id,
        "name": channel.name,
        "description": channel.description or f"Автоматически добавлен: {channel.name}",
        "enabled": True,
        "peer_type": channel.peer_type,
        "prompt_file": channel.prompt_file or default_prompt,
        "poll_interval_minutes": channel.poll_interval_minutes or 60,
        "consolidated_doc_path": consolidated_doc_path,
        "consolidated_doc_prompt_file": "prompts/consolidated_engineering.md",
        "recipients": [
            {
                "telegram_id": r.telegram_id,
                "name": r.name,
                "role": r.role,
                "send_file": r.send_file,
                "send_text": r.send_text,
            }
            for r in channel.recipients
        ]
    }
    
    # Добавляем канал
    if 'channels' not in data:
        data['channels'] = []
    data['channels'].append(channel_data)
    
    # Сохраняем обратно
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    
    logger.info(f"Канал добавлен в конфигурацию. Сводный документ: {consolidated_doc_path}")
    
    # Обновляем channel.consolidated_doc_path для дальнейшей обработки
    channel.consolidated_doc_path = consolidated_doc_path


async def main():
    parser = argparse.ArgumentParser(description='Добавить новый чат в систему мониторинга')
    parser.add_argument('chat_id', type=int, help='Telegram ID чата (положительное для групп, отрицательное для каналов)')
    parser.add_argument('--name', type=str, help='Название чата (если не указано, будет получено автоматически)')
    parser.add_argument('--prompt', type=str, default='prompts/digest_management.md', help='Файл промпта для дайджестов')
    parser.add_argument('--recipient-id', type=int, help='Telegram ID получателя (если не указан, используется владелец сессии)')
    parser.add_argument('--recipient-name', type=str, help='Имя получателя')
    parser.add_argument('--config', type=str, help='Путь к channels.json')
    
    args = parser.parse_args()
    
    # Загружаем конфигурацию
    config_path = Path(args.config) if args.config else Path("config/channels.json")
    if not config_path.is_absolute():
        config_path = Path(__file__).parent.parent / config_path
    
    config = load_config(str(config_path))
    
    # Инициализируем сервисы
    db = Database(config)
    tg_service = TelegramService(config, db)
    
    try:
        await tg_service.connect()
        
        # Определяем тип чата и название
        peer_type, detected_name = await detect_chat_type(tg_service, args.chat_id)
        chat_name = args.name or detected_name
        
        logger.info(f"Обнаружен чат: {chat_name} (ID: {args.chat_id}, тип: {peer_type})")
        
        # Получаем ID получателя
        if args.recipient_id:
            recipient_id = args.recipient_id
            recipient_name = args.recipient_name or f"User {recipient_id}"
        else:
            recipient_id = await tg_service.get_me_user_id()
            me = await tg_service._client.get_me()
            recipient_name = args.recipient_name or f"{me.first_name} {me.last_name or ''}".strip()
        
        # Создаём объект Channel
        channel = Channel(
            id=args.chat_id,
            name=chat_name,
            description=f"Автоматически добавлен",
            enabled=True,
            peer_type=peer_type,
            prompt_file=args.prompt,
            poll_interval_minutes=60,
            recipients=[
                Recipient(
                    telegram_id=recipient_id,
                    name=recipient_name,
                    role="lead",
                    send_file=True,
                    send_text=True,
                )
            ],
            consolidated_doc_path="",  # Будет установлен в add_channel_to_config
        )
        
        # Инициализируем OCR сервис
        ocr_service = None
        if config.defaults.ocr_enabled:
            try:
                ocr_service = UnifiedOCRService(config, db)
            except Exception as e:
                logger.warning(f"OCR сервис недоступен: {e}")
        
        # 1. Загружаем всю историю
        total_messages, total_media = await load_full_history(
            tg_service, channel, db, ocr_service
        )
        
        # Обрабатываем OCR для всех загруженных медиа
        if ocr_service and total_media > 0:
            logger.info("Обработка OCR для загруженных медиафайлов...")
            processed = await ocr_service.process_pending_media_async(limit=1000)
            logger.info(f"Обработано OCR: {processed} медиафайлов")
        
        # 2. Добавляем канал в конфигурацию (чтобы установить consolidated_doc_path)
        add_channel_to_config(config_path, channel, args.prompt)
        
        # Перезагружаем конфигурацию чтобы получить consolidated_doc_path
        config = load_config(str(config_path))
        updated_channel = next((ch for ch in config.channels if ch.id == channel.id), None)
        if updated_channel:
            channel = updated_channel
        
        # 3. Создаём сводный документ
        llm_service = LLMService(config)
        worker = DigestWorker(config)
        worker.db = db
        worker.tg_service = tg_service
        worker.ocr_service = ocr_service
        worker.llm_service = llm_service
        
        if channel.consolidated_doc_path:
            await create_consolidated_doc(worker, channel, db)
        
        # 4. Индексируем в RAG если доступно
        if channel.consolidated_doc_path and vec_schema_exists(db):
            try:
                doc_path = config.repo_dir / channel.consolidated_doc_path
                if doc_path.exists():
                    doc_content = doc_path.read_text(encoding='utf-8')
                    index_consolidated_doc_to_rag(
                        config, db,
                        channel.peer_type, channel.id,
                        channel.consolidated_doc_path, doc_content
                    )
                    logger.info("Сводный документ проиндексирован в RAG")
            except Exception as e:
                logger.warning(f"Ошибка индексации в RAG: {e}")
        
        # 5. Уведомляем о завершении
        if config.tg_bot_token:
            try:
                from telegram_client import TelegramBot
                bot = TelegramBot(config)
                await bot.send_text(
                    recipient_id,
                    f"""✅ Чат добавлен в систему мониторинга!

📋 Название: {chat_name}
🆔 ID: {args.chat_id}
📊 Тип: {peer_type}

📥 Загружено:
   • Сообщений: {total_messages}
   • Медиафайлов: {total_media}

📄 Сводный документ: {channel.consolidated_doc_path or 'не создан'}

Система начнёт мониторинг новых сообщений автоматически."""
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление: {e}")
        
        logger.info("✅ Чат успешно добавлен в систему!")
        logger.info(f"   Сообщений: {total_messages}, Медиа: {total_media}")
        logger.info(f"   Сводный документ: {channel.consolidated_doc_path}")
        
    except Exception as e:
        logger.error(f"Ошибка добавления чата: {e}")
        logger.exception("Traceback")
        sys.exit(1)
    finally:
        await tg_service.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
