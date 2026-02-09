#!/usr/bin/env python3
"""
config_db.py — Загрузка конфигурации каналов из БД (для мультитенантности)
"""

import logging
from typing import Optional, List
from dataclasses import dataclass
import psycopg2
from psycopg2.extras import RealDictCursor

from config import Config, Channel, Recipient, Defaults

logger = logging.getLogger(__name__)


def get_prompt_from_db(config: Config, telegram_chat_id: int, prompt_type: str, user_id: Optional[int] = None) -> Optional[str]:
    """
    Загружает промпт из таблицы channel_prompts БД.
    
    Args:
        config: Конфигурация с параметрами подключения к БД
        telegram_chat_id: Telegram ID чата/канала
        prompt_type: Тип промпта ('digest' или 'consolidated')
        user_id: ID пользователя (опционально, для проверки принадлежности)
    
    Returns:
        Текст промпта или None если не найден
    """
    try:
        conn = psycopg2.connect(
            host=config.pg_host,
            port=config.pg_port,
            database=config.pg_database,
            user=config.pg_user,
            password=config.pg_password,
        )
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Сначала ищем промпт по умолчанию
            if user_id:
                cur.execute("""
                    SELECT cp.text
                    FROM channel_prompts cp
                    JOIN web_channels wc ON cp.channel_id = wc.id
                    WHERE wc.telegram_chat_id = %s 
                    AND cp.prompt_type = %s 
                    AND cp.is_default = true
                    AND wc.user_id = %s
                    ORDER BY cp.created_at DESC
                    LIMIT 1
                """, (telegram_chat_id, prompt_type, user_id))
            else:
                cur.execute("""
                    SELECT cp.text
                    FROM channel_prompts cp
                    JOIN web_channels wc ON cp.channel_id = wc.id
                    WHERE wc.telegram_chat_id = %s 
                    AND cp.prompt_type = %s 
                    AND cp.is_default = true
                    ORDER BY cp.created_at DESC
                    LIMIT 1
                """, (telegram_chat_id, prompt_type))
            
            result = cur.fetchone()
            if result:
                conn.close()
                logger.debug(f"Промпт {prompt_type} найден в БД (is_default=true) для канала {telegram_chat_id}")
                return result['text']
            
            # Если промпта по умолчанию нет, берём первый доступный
            if user_id:
                cur.execute("""
                    SELECT cp.text
                    FROM channel_prompts cp
                    JOIN web_channels wc ON cp.channel_id = wc.id
                    WHERE wc.telegram_chat_id = %s 
                    AND cp.prompt_type = %s
                    AND wc.user_id = %s
                    ORDER BY cp.created_at DESC
                    LIMIT 1
                """, (telegram_chat_id, prompt_type, user_id))
            else:
                cur.execute("""
                    SELECT cp.text
                    FROM channel_prompts cp
                    JOIN web_channels wc ON cp.channel_id = wc.id
                    WHERE wc.telegram_chat_id = %s 
                    AND cp.prompt_type = %s
                    ORDER BY cp.created_at DESC
                    LIMIT 1
                """, (telegram_chat_id, prompt_type))
            
            result = cur.fetchone()
            conn.close()
            
            if result:
                logger.debug(f"Промпт {prompt_type} найден в БД (первый доступный) для канала {telegram_chat_id}")
                return result['text']
        
    except Exception as e:
        logger.warning(f"Ошибка загрузки промпта из БД для канала {telegram_chat_id}, тип {prompt_type}: {e}")
    
    return None


def get_prompt_from_web_channels(config: Config, telegram_chat_id: int, prompt_type: str, user_id: Optional[int] = None) -> Optional[str]:
    """
    Загружает промпт из полей prompt_text или consolidated_doc_prompt_text таблицы web_channels (обратная совместимость).
    
    Args:
        config: Конфигурация с параметрами подключения к БД
        telegram_chat_id: Telegram ID чата/канала
        prompt_type: Тип промпта ('digest' или 'consolidated')
        user_id: ID пользователя (опционально)
    
    Returns:
        Текст промпта или None если не найден
    """
    try:
        conn = psycopg2.connect(
            host=config.pg_host,
            port=config.pg_port,
            database=config.pg_database,
            user=config.pg_user,
            password=config.pg_password,
        )
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if user_id:
                cur.execute("""
                    SELECT prompt_text, consolidated_doc_prompt_text
                    FROM web_channels
                    WHERE telegram_chat_id = %s AND user_id = %s
                """, (telegram_chat_id, user_id))
            else:
                cur.execute("""
                    SELECT prompt_text, consolidated_doc_prompt_text
                    FROM web_channels
                    WHERE telegram_chat_id = %s
                """, (telegram_chat_id,))
            
            result = cur.fetchone()
            conn.close()
            
            if result:
                if prompt_type == 'digest' and result.get('prompt_text'):
                    logger.debug(f"Промпт digest найден в web_channels.prompt_text для канала {telegram_chat_id}")
                    return result['prompt_text']
                elif prompt_type == 'consolidated' and result.get('consolidated_doc_prompt_text'):
                    logger.debug(f"Промпт consolidated найден в web_channels.consolidated_doc_prompt_text для канала {telegram_chat_id}")
                    return result['consolidated_doc_prompt_text']
        
    except Exception as e:
        logger.warning(f"Ошибка загрузки промпта из web_channels для канала {telegram_chat_id}: {e}")
    
    return None


def load_channels_from_db(config: Config) -> List[Channel]:
    """
    Загружает каналы из таблицы web_channels БД.
    
    Returns:
        List[Channel]: Список каналов из БД
    """
    channels = []
    
    try:
        conn = psycopg2.connect(
            host=config.pg_host,
            port=config.pg_port,
            database=config.pg_database,
            user=config.pg_user,
            password=config.pg_password,
        )
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Получаем только активные каналы
            cur.execute("""
                SELECT 
                    wc.*,
                    u.telegram_id as user_telegram_id
                FROM web_channels wc
                JOIN users u ON u.id = wc.user_id
                WHERE wc.enabled = true
                ORDER BY wc.created_at
            """)
            
            rows = cur.fetchall()
            
            for row in rows:
                # Создаём получателя
                recipient = Recipient(
                    telegram_id=row['recipient_telegram_id'],
                    name=row['recipient_name'] or f"User {row['recipient_telegram_id']}",
                    role="user",
                    send_file=True,
                    send_text=True,
                )
                
                # Создаём канал
                channel = Channel(
                    id=row['telegram_chat_id'],
                    name=row['name'],
                    description=row['description'] or "",
                    enabled=row['enabled'],
                    peer_type=row['peer_type'],
                    prompt_file=row['prompt_file'],
                    poll_interval_minutes=row['poll_interval_minutes'],
                    recipients=[recipient],
                    consolidated_doc_path=row['consolidated_doc_path'] or "",
                    consolidated_doc_prompt_file=row.get('consolidated_doc_prompt_file', 'prompts/consolidated_engineering.md'),
                )
                
                # Сохраняем user_id в канале (через атрибут)
                channel.user_id = row['user_id']
                channel.user_telegram_id = row['user_telegram_id']
                # Настройки доставки дайджеста (миграция 008)
                channel.delivery_importance = row.get('delivery_importance') or 'important'
                channel.delivery_send_file = row.get('delivery_send_file', True)
                channel.delivery_send_text = row.get('delivery_send_text', True)
                channel.delivery_text_max_chars = row.get('delivery_text_max_chars')
                channel.delivery_summary_only = row.get('delivery_summary_only', False)
                
                channels.append(channel)
        
        conn.close()
        logger.info(f"Загружено {len(channels)} каналов из БД")
        
    except Exception as e:
        logger.error(f"Ошибка загрузки каналов из БД: {e}")
    
    return channels


def merge_channels_from_sources(config: Config) -> List[Channel]:
    """
    Объединяет каналы из channels.json и БД.
    Каналы из БД имеют приоритет (если есть дубликаты по ID).
    
    Returns:
        List[Channel]: Объединённый список каналов
    """
    # Загружаем из файла (legacy)
    file_channels = config.channels
    
    # Загружаем из БД
    db_channels = load_channels_from_db(config)
    
    # Объединяем: каналы из БД + каналы из файла которых нет в БД
    db_channel_ids = {ch.id for ch in db_channels}
    merged = db_channels + [ch for ch in file_channels if ch.id not in db_channel_ids]
    
    logger.info(f"Объединено каналов: {len(file_channels)} из файла + {len(db_channels)} из БД = {len(merged)} всего")
    
    return merged
