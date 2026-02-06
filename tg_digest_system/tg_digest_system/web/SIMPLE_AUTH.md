# Простая система идентификации для внутреннего использования

## Требования

- Узкий круг пользователей внутри фирмы
- Все в одних чатах
- Доверенная среда
- Основная цель: удобство использования, а не защита от злоумышленников

## Предлагаемое решение: Простая сессия через Cookie

### Логика:

1. **Первый вход:**
   - Пользователь вводит свой Telegram ID
   - Система создаёт сессию (уникальный токен)
   - Сохраняет в БД: `user_id`, `session_token`, `created_at`
   - Устанавливает cookie `session_token` (срок действия 30 дней)

2. **Последующие запросы:**
   - Система читает `session_token` из cookie
   - Проверяет что сессия существует и не истекла
   - Получает `user_id` из сессии
   - Выполняет запрос от имени этого пользователя

3. **Если сессии нет:**
   - Пользователь снова вводит Telegram ID
   - Создаётся новая сессия

### Преимущества:

- ✅ Минимальная реализация
- ✅ Удобно для пользователя (не нужно вводить ID каждый раз)
- ✅ Изоляция данных по user_id сохраняется
- ✅ Можно добавить "Выйти" для очистки сессии

### Что НЕ проверяем (для простоты):

- ❌ IP адрес (может меняться)
- ❌ Device fingerprint (сложно)
- ❌ Двухфакторная аутентификация (не нужно)

### Структура БД:

```sql
CREATE TABLE user_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_token TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_user_sessions_token ON user_sessions(session_token);
CREATE INDEX idx_user_sessions_user ON user_sessions(user_id);
CREATE INDEX idx_user_sessions_expires ON user_sessions(expires_at);
```

### Поток работы:

```
1. Пользователь открывает сайт
   ↓
2. Система проверяет cookie session_token
   ↓
3. Если сессия есть и валидна:
   → Используем user_id из сессии
   → Показываем интерфейс
   ↓
4. Если сессии нет:
   → Показываем форму ввода Telegram ID
   → После ввода создаём сессию
   → Устанавливаем cookie
   → Показываем интерфейс
```

### Безопасность (минимальная):

- Токен сессии - случайная строка (32+ символа)
- Срок действия сессии - 30 дней
- Автоматическая очистка истекших сессий
- При удалении пользователя удаляются все его сессии

### Дополнительные возможности (опционально):

1. **Кнопка "Выйти"** - очищает cookie и удаляет сессию
2. **"Запомнить меня"** - продлевает срок действия до 90 дней
3. **Список активных сессий** - пользователь может видеть где он залогинен

## Реализация

### 1. Миграция БД

Добавить таблицу `user_sessions`

### 2. Функции для работы с сессиями

- `create_session(user_id)` - создаёт сессию
- `get_user_from_session(token)` - получает user_id по токену
- `delete_session(token)` - удаляет сессию
- `cleanup_expired_sessions()` - очищает истекшие сессии

### 3. Middleware/Dependency

- Проверяет cookie на каждом запросе
- Если сессия валидна - добавляет `user_id` в request
- Если нет - возвращает форму входа

### 4. Модификация endpoints

- Убрать `user_telegram_id` из query параметров
- Использовать `user_id` из сессии
- Если сессии нет - редирект на страницу входа

### 5. Страница входа

- Простая форма с полем "Telegram ID"
- После ввода создаёт сессию и редиректит

## Пример использования:

```python
# Dependency для получения текущего пользователя
async def get_current_user(request: Request, db=Depends(get_db)) -> Optional[int]:
    session_token = request.cookies.get("session_token")
    if not session_token:
        return None
    
    return get_user_from_session(session_token, db)

# Endpoint с проверкой сессии
@app.get("/api/channels")
async def list_channels(
    current_user: int = Depends(get_current_user),
    db=Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    
    # Используем current_user вместо user_telegram_id
    ...
```

## Альтернатива (ещё проще): Только Cookie без БД

Если не нужна даже таблица сессий, можно хранить зашифрованный `user_id` прямо в cookie:

```python
# Создание cookie
user_data = {"user_id": user_id, "telegram_id": telegram_id}
encrypted = encrypt(json.dumps(user_data))  # Простое шифрование
response.set_cookie("user_session", encrypted, max_age=2592000)

# Проверка
encrypted = request.cookies.get("user_session")
user_data = json.loads(decrypt(encrypted))
user_id = user_data["user_id"]
```

Но это менее безопасно, хотя для внутреннего использования может быть достаточно.
