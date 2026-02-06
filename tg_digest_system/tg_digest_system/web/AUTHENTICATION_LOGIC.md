# Логика валидации и проверки пользователя

## Текущая ситуация (без валидации)

### Как работает сейчас:

1. **Пользователь вводит Telegram ID** в форме
2. **Система создаёт/получает пользователя** в БД по Telegram ID
3. **Нет проверки** что это действительно тот пользователь
4. **Любой может указать чужой Telegram ID** и получить доступ к чужим данным

### Проблемы:

- ❌ Нет аутентификации
- ❌ Нет проверки владельца Telegram ID
- ❌ Любой может указать чужой Telegram ID
- ❌ Нет защиты от несанкционированного доступа

## Варианты валидации с использованием клиентского компьютера

### Вариант 1: IP-адрес + Device Fingerprint (Рекомендуется)

**Как работает:**
1. При первом входе сохраняем:
   - IP адрес клиента
   - User-Agent браузера
   - Device fingerprint (разрешение экрана, часовой пояс, языки и т.д.)
   - Хэш комбинации этих данных
2. При последующих запросах проверяем соответствие
3. Если IP/Device изменился - требуем подтверждение через Telegram

**Преимущества:**
- ✅ Простая реализация
- ✅ Работает без cookies
- ✅ Защита от базовых атак
- ✅ Можно использовать для ограничения доступа

**Недостатки:**
- ❌ IP может меняться (мобильный интернет, VPN)
- ❌ Несколько устройств = несколько записей
- ❌ Можно обойти через VPN

**Реализация:**
```python
def get_client_fingerprint(request: Request) -> str:
    """Генерирует fingerprint клиента"""
    ip = request.client.host
    user_agent = request.headers.get("user-agent", "")
    accept_language = request.headers.get("accept-language", "")
    # Можно добавить больше заголовков
    
    fingerprint_data = f"{ip}|{user_agent}|{accept_language}"
    return hashlib.sha256(fingerprint_data.encode()).hexdigest()

def validate_user_device(user_id: int, fingerprint: str, db) -> bool:
    """Проверяет что устройство авторизовано для пользователя"""
    # Проверяем в БД есть ли запись для этого user_id + fingerprint
    # Если нет - создаём новую или требуем подтверждения
    pass
```

### Вариант 2: Сессия через Cookies + IP

**Как работает:**
1. При первом входе создаём сессию:
   - Генерируем уникальный токен сессии
   - Сохраняем в БД: `user_id`, `session_token`, `ip_address`, `created_at`
   - Устанавливаем cookie с токеном
2. При каждом запросе:
   - Читаем токен из cookie
   - Проверяем что сессия валидна и IP совпадает
   - Обновляем время последнего доступа

**Преимущества:**
- ✅ Стандартный подход
- ✅ Удобно для пользователя (не нужно вводить ID каждый раз)
- ✅ Можно добавить "Запомнить меня"

**Недостатки:**
- ❌ Требует работы с cookies
- ❌ IP может меняться (нужна гибкая проверка)
- ❌ Нужна очистка истекших сессий

**Реализация:**
```python
from fastapi import Request, Response
import secrets
from datetime import datetime, timedelta

def create_session(user_id: int, ip: str, db) -> str:
    """Создаёт сессию для пользователя"""
    session_token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(days=30)
    
    # Сохраняем в БД
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO user_sessions (user_id, session_token, ip_address, expires_at)
            VALUES (%s, %s, %s, %s)
        """, (user_id, session_token, ip, expires_at))
    
    return session_token

def validate_session(request: Request, db) -> Optional[int]:
    """Проверяет сессию и возвращает user_id"""
    session_token = request.cookies.get("session_token")
    if not session_token:
        return None
    
    ip = request.client.host
    
    with db.cursor() as cur:
        cur.execute("""
            SELECT user_id FROM user_sessions
            WHERE session_token = %s 
            AND ip_address = %s
            AND expires_at > now()
        """, (session_token, ip))
        
        row = cur.fetchone()
        return row[0] if row else None
```

### Вариант 3: Telegram Web App (Самый надёжный)

**Как работает:**
1. Пользователь открывает веб-интерфейс через Telegram Web App
2. Telegram автоматически передаёт:
   - `initData` - подписанные данные пользователя
   - `user` - объект с Telegram ID, username и т.д.
3. Сервер проверяет подпись Telegram
4. Если подпись валидна - пользователь авторизован

**Преимущества:**
- ✅ Официальная валидация от Telegram
- ✅ Невозможно подделать
- ✅ Не нужно вводить Telegram ID вручную
- ✅ Telegram сам проверяет пользователя

**Недостатки:**
- ❌ Работает только через Telegram Web App
- ❌ Нужна интеграция с Telegram Bot API
- ❌ Не работает если открыть сайт напрямую в браузере

**Реализация:**
```python
from telegram import Bot
from telegram.utils.web_app_data import validate_web_app_data

async def validate_telegram_webapp(request: Request) -> Optional[int]:
    """Валидирует данные от Telegram Web App"""
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data:
        return None
    
    bot_token = os.environ.get("TG_BOT_TOKEN")
    bot = Bot(token=bot_token)
    
    try:
        # Telegram проверяет подпись
        data = validate_web_app_data(bot.token, init_data)
        user_id = data.get("user", {}).get("id")
        return user_id
    except:
        return None
```

### Вариант 4: Комбинированный (IP + Device + Telegram верификация)

**Как работает:**
1. **Первый вход:**
   - Пользователь вводит Telegram ID
   - Система отправляет код подтверждения в Telegram (через бота)
   - Пользователь вводит код на сайте
   - Сохраняем: `user_id`, `ip`, `device_fingerprint`, `verified_at`

2. **Последующие входы:**
   - Проверяем IP + Device fingerprint
   - Если совпадает - автоматический вход
   - Если не совпадает - требуем код подтверждения

**Преимущества:**
- ✅ Двухфакторная аутентификация
- ✅ Работает с любого устройства
- ✅ Защита от подмены

**Недостатки:**
- ❌ Требует интеграции с Telegram Bot API
- ❌ Пользователь должен иметь доступ к Telegram

## Рекомендация для текущего этапа

### Вариант: IP + Device Fingerprint + Сессия (упрощённый)

**Логика:**

1. **При первом добавлении канала:**
   - Сохраняем `user_id`, `ip_address`, `device_fingerprint`
   - Генерируем сессию (cookie)
   - Запоминаем устройство

2. **При последующих запросах:**
   - Проверяем сессию из cookie
   - Если сессия есть и IP совпадает → доступ разрешён
   - Если IP изменился → требуем подтверждение (можно через Telegram код)
   - Если сессии нет → пользователь вводит Telegram ID заново

3. **Защита:**
   - Один пользователь может иметь несколько устройств
   - Каждое устройство = отдельная сессия
   - При смене IP можно требовать подтверждение

**Таблица БД:**
```sql
CREATE TABLE user_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_token TEXT UNIQUE NOT NULL,
    ip_address INET NOT NULL,
    device_fingerprint TEXT,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    UNIQUE(user_id, device_fingerprint)
);

CREATE INDEX idx_user_sessions_token ON user_sessions(session_token);
CREATE INDEX idx_user_sessions_user ON user_sessions(user_id);
```

## Что нужно реализовать

1. **Модифицировать `get_or_create_user()`** - добавить проверку устройства
2. **Создать таблицу `user_sessions`** - для хранения сессий
3. **Добавить middleware** - для проверки сессии на каждом запросе
4. **Модифицировать endpoints** - использовать сессию вместо `user_telegram_id` в query
5. **Добавить страницу входа** - если сессии нет

## Вопросы для уточнения

1. Нужна ли защита от смены IP? (мобильный интернет, VPN)
2. Сколько устройств может быть у одного пользователя?
3. Нужна ли возможность "выйти" и очистить сессию?
4. Нужна ли двухфакторная аутентификация через Telegram код?
