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
