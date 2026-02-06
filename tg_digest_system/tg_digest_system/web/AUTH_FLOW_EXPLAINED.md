# Детальное объяснение потока авторизации

## Полный процесс от начала до конца

### Шаг 1: Пользователь впервые открывает сайт

```
Пользователь → Открывает http://158.160.19.253/
              ↓
Система проверяет: есть ли cookie "session_token"?
              ↓
НЕТ (первый раз) → Показываем форму ввода Telegram ID
```

**Что происходит в коде:**
```python
@app.get("/")
async def index(request: Request):
    # Проверяем есть ли сессия
    session_token = request.cookies.get("session_token")
    
    if session_token:
        # Есть сессия - проверяем валидна ли она
        user_id = get_user_from_session(session_token, db)
        if user_id:
            # Сессия валидна - показываем главную страницу
            return templates.TemplateResponse("index.html", {"request": request})
    
    # Сессии нет или невалидна - показываем форму входа
    return templates.TemplateResponse("login.html", {"request": request})
```

### Шаг 2: Пользователь вводит Telegram ID и отправляет форму

```
Пользователь → Вводит: 499412926
              ↓
Нажимает "Войти"
              ↓
POST /api/login {telegram_id: 499412926}
```

**Что происходит в коде:**
```python
@app.post("/api/login")
async def login(
    telegram_id: int = Form(...),
    db=Depends(get_db),
    response: Response = None
):
    # 1. Получаем или создаём пользователя в БД
    user_id = get_or_create_user(db, telegram_id, None)
    
    # 2. Создаём сессию (генерируем случайный токен)
    session_token = create_session(user_id, db)
    # Например: "xK9mP2qR7vN4tY8wZ1aB3cD5eF6gH0jK2lM4nO6pQ8rS0tU2vW4xY6zA8bC0dE"
    
    # 3. Сохраняем в БД:
    # INSERT INTO user_sessions (user_id, session_token, expires_at)
    # VALUES (1, 'xK9mP2qR7vN4tY8wZ1aB3cD5eF6gH0jK2lM4nO6pQ8rS0tU2vW4xY6zA8bC0dE', '2026-03-08')
    
    # 4. Устанавливаем cookie в ответе браузеру
    set_session_cookie(response, session_token)
    # Браузер сохраняет: session_token=xK9mP2qR7vN4tY8wZ1aB3cD5eF6gH0jK2lM4nO6pQ8rS0tU2vW4xY6zA8bC0dE
    
    # 5. Редирект на главную страницу
    return RedirectResponse(url="/", status_code=303)
```

**Что сохраняется в БД:**
```sql
-- Таблица user_sessions
user_id | session_token                                    | expires_at
--------|--------------------------------------------------|------------
1       | xK9mP2qR7vN4tY8wZ1aB3cD5eF6gH0jK2lM4nO6pQ8rS0tU | 2026-03-08
```

**Что сохраняется в браузере:**
```
Cookie: session_token=xK9mP2qR7vN4tY8wZ1aB3cD5eF6gH0jK2lM4nO6pQ8rS0tU2vW4xY6zA8bC0dE
```

### Шаг 3: Пользователь делает следующий запрос (например, открывает список каналов)

```
Пользователь → Открывает http://158.160.19.253/channels
              ↓
Браузер автоматически отправляет cookie с каждым запросом:
Cookie: session_token=xK9mP2qR7vN4tY8wZ1aB3cD5eF6gH0jK2lM4nO6pQ8rS0tU2vW4xY6zA8bC0dE
```

**Что происходит в коде:**
```python
@app.get("/api/channels")
async def list_channels(
    current_user: int = Depends(require_auth),  # ← Здесь происходит проверка!
    db=Depends(get_db)
):
    # current_user уже содержит user_id=1 (из сессии)
    # Можно сразу использовать для запроса к БД
    ...
```

**Как работает `require_auth` dependency:**
```python
def require_auth(current_user: Optional[int] = Depends(get_current_user)) -> int:
    # get_current_user вызывается автоматически FastAPI
    # Он получает request и проверяет cookie
    
    if not current_user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    return current_user

async def get_current_user(request: Request, db=Depends(get_db)) -> Optional[int]:
    # 1. Читаем cookie из запроса
    session_token = request.cookies.get("session_token")
    # session_token = "xK9mP2qR7vN4tY8wZ1aB3cD5eF6gH0jK2lM4nO6pQ8rS0tU2vW4xY6zA8bC0dE"
    
    if not session_token:
        return None  # Cookie нет - пользователь не авторизован
    
    # 2. Ищем сессию в БД
    # SELECT user_id FROM user_sessions
    # WHERE session_token = 'xK9mP2qR7vN4tY8wZ1aB3cD5eF6gH0jK2lM4nO6pQ8rS0tU2vW4xY6zA8bC0dE'
    # AND expires_at > now()
    
    # 3. Если нашли - возвращаем user_id
    # Если не нашли (сессия истекла или удалена) - возвращаем None
    
    return get_user_from_session(session_token, db)  # Возвращает user_id=1 или None
```

### Шаг 4: Проверка валидности сессии (детально)

**Функция `get_user_from_session()`:**
```python
def get_user_from_session(session_token: str, db) -> Optional[int]:
    # 1. Проверяем что токен не пустой
    if not session_token:
        return None
    
    # 2. Ищем сессию в БД
    with db.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT user_id FROM user_sessions
            WHERE session_token = %s           -- Токен совпадает?
            AND expires_at > now()              -- И срок не истёк?
        """, (session_token,))
        
        row = cur.fetchone()
        
        if row:
            # Сессия найдена и валидна!
            user_id = row['user_id']  # Например: 1
            
            # Обновляем время последнего использования
            cur.execute("""
                UPDATE user_sessions 
                SET last_used_at = now()
                WHERE session_token = %s
            """, (session_token,))
            db.commit()
            
            return user_id  # Возвращаем user_id=1
        else:
            # Сессия не найдена или истекла
            return None
```

## Визуализация потока данных

```
┌─────────────┐
│  Браузер    │
│  Пользователя│
└──────┬──────┘
       │
       │ 1. GET /api/channels
       │    Cookie: session_token=abc123...
       │
       ▼
┌─────────────────────────────────┐
│  FastAPI Endpoint               │
│  @app.get("/api/channels")      │
│  current_user = Depends(        │
│    require_auth                 │
│  )                              │
└──────┬──────────────────────────┘
       │
       │ 2. Вызывается get_current_user()
       │
       ▼
┌─────────────────────────────────┐
│  get_current_user()              │
│  - Читает cookie                │
│  - Вызывает get_user_from_session│
└──────┬──────────────────────────┘
       │
       │ 3. SELECT FROM user_sessions
       │    WHERE session_token = 'abc123...'
       │    AND expires_at > now()
       │
       ▼
┌─────────────────────────────────┐
│  PostgreSQL                      │
│  Таблица user_sessions           │
│                                  │
│  user_id | session_token | ...   │
│  1       | abc123...     | ...   │
└──────┬──────────────────────────┘
       │
       │ 4. Возвращает user_id=1
       │
       ▼
┌─────────────────────────────────┐
│  require_auth()                  │
│  - Проверяет что user_id не None │
│  - Возвращает user_id=1          │
└──────┬──────────────────────────┘
       │
       │ 5. current_user = 1
       │
       ▼
┌─────────────────────────────────┐
│  list_channels()                 │
│  - Использует current_user=1     │
│  - Делает запрос к БД           │
│  - Возвращает каналы user_id=1   │
└─────────────────────────────────┘
```

## Примеры сценариев

### Сценарий 1: Валидная сессия

```
1. Пользователь открывает /channels
2. Браузер отправляет: Cookie: session_token=abc123...
3. Система находит в БД: user_id=1, expires_at=2026-03-08 (ещё не истёк)
4. Результат: ✅ current_user = 1
5. Показываются каналы пользователя с user_id=1
```

### Сценарий 2: Сессия истекла

```
1. Пользователь открывает /channels
2. Браузер отправляет: Cookie: session_token=abc123...
3. Система ищет в БД: expires_at=2026-01-01 (уже истёк!)
4. Результат: ❌ current_user = None
5. Возвращается HTTP 401 "Требуется авторизация"
6. Показывается форма входа
```

### Сценарий 3: Cookie отсутствует

```
1. Пользователь открывает /channels (впервые или после очистки cookies)
2. Браузер НЕ отправляет cookie
3. Система: session_token = None
4. Результат: ❌ current_user = None
5. Возвращается HTTP 401
6. Показывается форма входа
```

### Сценарий 4: Неправильный токен

```
1. Пользователь открывает /channels
2. Браузер отправляет: Cookie: session_token=wrong_token
3. Система ищет в БД: не находит (такого токена нет)
4. Результат: ❌ current_user = None
5. Возвращается HTTP 401
6. Показывается форма входа
```

## Ключевые моменты

1. **Токен генерируется один раз** при входе и сохраняется в БД
2. **Браузер автоматически отправляет cookie** с каждым запросом
3. **Система проверяет токен в БД** при каждом запросе
4. **Если токен найден и не истёк** → пользователь авторизован
5. **Если токен не найден или истёк** → требуется повторный вход

## Безопасность (минимальная, для внутреннего использования)

- ✅ Токен случайный (32+ символа) - нельзя угадать
- ✅ Токен хранится в БД - можно отследить и удалить
- ✅ Срок действия ограничен - автоматически истекает
- ✅ HttpOnly cookie - защита от XSS (JavaScript не может прочитать)

Но:
- ❌ Нет проверки IP - можно использовать с другого компьютера
- ❌ Нет двухфакторной аутентификации
- ❌ Если кто-то узнает токен - может использовать его

Для внутреннего использования этого достаточно.
