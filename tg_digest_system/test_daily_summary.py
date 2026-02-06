import sys
import os
sys.path.insert(0, 'tg_digest_system/scripts')
os.environ['CONFIG_FILE'] = '/home/ripas/tg_digest_system/tg_digest_system/config/channels.json'
import pytz
from datetime import datetime, timezone
from config import load_config
from database import Database
from digest_worker import DigestWorker
import asyncio

async def test_daily_summary():
    print('=== Тест ежедневного сводного дайджеста ===')
    config = load_config()
    
    db = Database(config)
    worker = DigestWorker(config)
    
    # Проверяем методы работы с датами
    msk_tz = pytz.timezone('Europe/Moscow')
    now_msk = datetime.now(msk_tz)
    print(f'Текущее время МСК: {now_msk.strftime("%Y-%m-%d %H:%M:%S")}')
    
    # Проверяем метод проверки времени
    is_time = worker._is_daily_summary_time()
    print(f'Время для ежедневного дайджеста (21:00-21:05): {is_time}')
    
    # Проверяем метод получения диапазона дат
    date_start, date_end = worker._get_daily_date_range()
    print(f'Начало дня (UTC): {date_start}')
    print(f'Конец дня (UTC): {date_end}')
    print(f'Начало дня (МСК): {date_start.astimezone(msk_tz)}')
    print(f'Конец дня (МСК): {date_end.astimezone(msk_tz)}')
    
    # Проверяем получение сообщений за день
    channel = config.channels[0]
    messages = db.get_messages_by_date(
        channel.peer_type, channel.id, date_start, date_end
    )
    print(f'Сообщений за сегодня: {len(messages)}')
    
    # Проверяем форматирование RAW дайджеста
    raw_digest = worker._format_daily_raw_digest(
        channel, messages, date_start, date_end
    )
    print(f'\nRAW дайджест (первые 500 символов):')
    print(raw_digest[:500])
    
    print('\n=== Тест завершён успешно ===')

asyncio.run(test_daily_summary())
