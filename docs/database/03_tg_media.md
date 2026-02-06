# tg.media

Хранение всех медиафайлов Telegram.

## Поля
- peer_type TEXT
- peer_id BIGINT
- msg_id BIGINT
- media_type TEXT
- local_path TEXT
- mime TEXT
- size_bytes BIGINT
- created_at TIMESTAMPTZ

## Назначение
Связь Telegram-сообщений с локально сохранёнными файлами.
