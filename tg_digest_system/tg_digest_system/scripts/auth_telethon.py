#!/usr/bin/env python3
"""
Скрипт для создания сессии Telethon
Использование: python auth_telethon.py
"""
import asyncio
from telethon import TelegramClient
import os

async def auth():
    session_file = os.environ.get('TG_SESSION_FILE', '/app/data/telethon.session')
    api_id = int(os.environ.get('TG_API_ID', '0'))
    api_hash = os.environ.get('TG_API_HASH', '')
    
    if not api_id or not api_hash:
        print("Ошибка: TG_API_ID и TG_API_HASH должны быть установлены")
        return
    
    client = TelegramClient(
        session_file,
        api_id,
        api_hash
    )
    
    # Используем интерактивный режим для ввода номера телефона
    await client.start(phone=lambda: input('Please enter your phone (or bot token): '))
    me = await client.get_me()
    print(f'\n✅ Авторизован как: {me.first_name} (ID: {me.id})')
    print(f'Сессия сохранена в: {session_file}')
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(auth())
