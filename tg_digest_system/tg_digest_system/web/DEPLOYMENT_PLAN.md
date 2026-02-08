# План безопасного деплоя веб-интерфейса

## Цели деплоя

- ✅ Развернуть веб-интерфейс на сервере
- ✅ Сохранить все существующие данные
- ✅ Сохранить все инженерные документы
- ✅ Не прервать работу основного воркера
- ✅ Возможность отката при проблемах

## Предварительная подготовка

### 1. Бэкап данных

**Обязательно сделать бэкап перед деплоем:**

```bash
# Бэкап БД
pg_dump -U tg_digest -d tg_digest -F c -f backup_before_web_deploy_$(date +%Y%m%d_%H%M%S).dump

# Бэкап инженерных документов
tar -czf docs_backup_$(date +%Y%m%d_%H%M%S).tar.gz /home/ripas/tg_digest_system/docs/

# Бэкап конфигурации
cp /home/ripas/tg_digest_system/tg_digest_system/config/channels.json \
   /home/ripas/tg_digest_system/config/channels.json.backup_$(date +%Y%m%d_%H%M%S)
```

### 2. Проверка текущего состояния

```bash
# Проверить что воркер работает
systemctl status tg_digest_worker

# Проверить количество каналов в БД (если есть)
psql -U tg_digest -d tg_digest -c "SELECT COUNT(*) FROM tg.messages;"

# Проверить существующие инженерные документы
ls -la /home/ripas/tg_digest_system/docs/reference/
```

## Пошаговый план деплоя

### Этап 1: Подготовка окружения (без остановки воркера)

**1.1. Установка зависимостей для веб-интерфейса**

```bash
cd /home/ripas/tg_digest_system/tg_digest_system/web
pip install -r requirements.txt
```

**1.2. Проверка переменных окружения**

Убедиться что в `.env` есть все необходимые переменные:
- `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`
- `TG_API_ID`, `TG_API_HASH`, `TG_SESSION_FILE`, `TG_BOT_TOKEN`
- `REPO_DIR` (путь к репозиторию с документами)

**1.3. Проверка доступа к БД**

```bash
psql -U tg_digest -d tg_digest -c "SELECT version();"
```

### Этап 2: Применение миграций БД (критический этап)

**2.1. Проверка существующих данных**

```bash
# Проверить есть ли уже таблица users
psql -U tg_digest -d tg_digest -c "\dt users"

# Проверить есть ли user_id в таблицах
psql -U tg_digest -d tg_digest -c "\d tg.messages" | grep user_id
```

**2.2. Применение миграции 001 (мультитенантность)**

```bash
cd /home/ripas/tg_digest_system/tg_digest_system/db/migrations
psql -U tg_digest -d tg_digest -f 001_add_user_id.sql
```

**Что делает миграция:**
- Создаёт таблицу `users` если её нет
- Создаёт пользователя с `id=1` и `telegram_id=499412926` (основной пользователь)
- Добавляет `user_id` во все таблицы
- **Автоматически присваивает всем существующим данным `user_id=1`**
- Создаёт индексы

**Важно:** Миграция безопасна - она не удаляет данные, только добавляет `user_id=1` к существующим записям.

**2.3. Проверка после миграции 001**

```bash
# Проверить что все сообщения получили user_id
psql -U tg_digest -d tg_digest -c "
SELECT 
    COUNT(*) as total,
    COUNT(user_id) as with_user_id,
    COUNT(*) FILTER (WHERE user_id IS NULL) as without_user_id
FROM tg.messages;
"
# Должно быть: with_user_id = total, without_user_id = 0

# Проверить что основной пользователь создан
psql -U tg_digest -d tg_digest -c "SELECT * FROM users WHERE id = 1;"
```

**2.4. Применение миграции 002 (сессии)**

```bash
psql -U tg_digest -d tg_digest -f 002_add_user_sessions.sql
```

**Что делает миграция:**
- Создаёт таблицу `user_sessions` для хранения сессий
- Создаёт функцию очистки истекших сессий
- **Не затрагивает существующие данные**

### Этап 3: Развёртывание веб-интерфейса

**3.1. Копирование файлов**

```bash
# Файлы уже должны быть на сервере (из git pull)
cd /home/ripas/tg_digest_system
git pull origin main  # или gitlab main

# Проверить что файлы на месте
ls -la tg_digest_system/tg_digest_system/web/web_api.py
ls -la tg_digest_system/tg_digest_system/web/templates/
```

**3.2. Настройка nginx (опционально)**

```bash
# Скопировать конфигурацию
sudo cp tg_digest_system/tg_digest_system/web/nginx.conf.example \
        /etc/nginx/sites-available/tg_digest_web

# Отредактировать пути если нужно
sudo nano /etc/nginx/sites-available/tg_digest_web

# Активировать
sudo ln -s /etc/nginx/sites-available/tg_digest_web \
           /etc/nginx/sites-enabled/

# Проверить конфигурацию
sudo nginx -t

# Перезагрузить nginx
sudo systemctl reload nginx
```

**3.3. Создание systemd service**

```bash
# Скопировать unit файл
sudo cp tg_digest_system/tg_digest_system/web/tg_digest_web.service.example \
        /etc/systemd/system/tg_digest_web.service

# Отредактировать пути если нужно
sudo nano /etc/systemd/system/tg_digest_web.service

# Перезагрузить systemd
sudo systemctl daemon-reload

# Включить автозапуск
sudo systemctl enable tg_digest_web
```

**3.4. Тестовый запуск веб-сервера**

```bash
# Запустить вручную для проверки
cd /home/ripas/tg_digest_system/tg_digest_system/web
source ../.env  # Загрузить переменные окружения
python web_api.py

# Проверить что сервер запустился
curl http://localhost:8080/health
```

**3.5. Запуск через systemd**

```bash
# Запустить сервис
sudo systemctl start tg_digest_web

# Проверить статус
sudo systemctl status tg_digest_web

# Проверить логи
sudo journalctl -u tg_digest_web -n 50
```

### Этап 4: Проверка работы

**4.1. Проверка веб-интерфейса**

```bash
# Проверить доступность
curl http://localhost:8080/health
curl http://localhost:8080/

# Если nginx настроен:
curl http://158.160.19.253/health
```

**4.2. Проверка что воркер продолжает работать**

```bash
systemctl status tg_digest_worker
journalctl -u tg_digest_worker -n 20
```

**4.3. Проверка что данные не потеряны**

```bash
# Проверить что сообщения на месте
psql -U tg_digest -d tg_digest -c "
SELECT COUNT(*) FROM tg.messages WHERE user_id = 1;
"

# Проверить что инженерные документы на месте
ls -la /home/ripas/tg_digest_system/docs/reference/
```

**4.4. Тест добавления канала через веб-интерфейс**

1. Открыть http://158.160.19.253/
2. Ввести Telegram ID
3. Добавить тестовый канал (который уже есть в системе)
4. Проверить что канал появился в списке
5. Проверить что воркер подхватил канал

### Этап 5: Интеграция с воркером

**5.1. Проверка что воркер загружает каналы из БД**

Воркер уже модифицирован для загрузки каналов из БД через `config_db.py`.
Нужно убедиться что:
- Файл `config_db.py` на месте
- Воркер импортирует `merge_channels_from_sources`
- Воркер вызывает эту функцию в `run_once()`

**5.2. Перезапуск воркера (если нужно)**

```bash
# Если воркер не подхватывает изменения автоматически
sudo systemctl restart tg_digest_worker

# Проверить логи
journalctl -u tg_digest_worker -n 50 | grep -i "каналов\|channels"
```

## План отката (на случай проблем)

### Если что-то пошло не так:

**1. Остановить веб-интерфейс**

```bash
sudo systemctl stop tg_digest_web
```

**2. Откатить миграции БД (если нужно)**

```sql
-- ВНИМАНИЕ: Это удалит user_id из таблиц!
-- Использовать только если действительно нужно откатить

-- Откат миграции 002
DROP TABLE IF EXISTS user_sessions CASCADE;
DROP FUNCTION IF EXISTS cleanup_expired_sessions();

-- Откат миграции 001 (частичный)
-- Удалить user_id из таблиц (но данные сохранятся)
ALTER TABLE tg.messages DROP COLUMN IF EXISTS user_id;
ALTER TABLE tg.media DROP COLUMN IF EXISTS user_id;
-- и т.д. для всех таблиц
```

**3. Восстановить бэкап БД**

```bash
pg_restore -U tg_digest -d tg_digest -c backup_before_web_deploy_*.dump
```

**4. Восстановить документы**

```bash
tar -xzf docs_backup_*.tar.gz -C /
```

## Риски и меры предосторожности

### Риск 1: Потеря данных при миграции

**Вероятность:** Низкая (миграция только добавляет поля)

**Меры:**
- ✅ Обязательный бэкап перед миграцией
- ✅ Миграция не удаляет данные, только добавляет `user_id=1`
- ✅ Проверка после миграции

### Риск 2: Конфликт с работающим воркером

**Вероятность:** Низкая (воркер работает независимо)

**Меры:**
- ✅ Воркер не останавливается во время деплоя
- ✅ Миграции применяются когда воркер работает
- ✅ Воркер автоматически подхватывает изменения

### Риск 3: Потеря инженерных документов

**Вероятность:** Очень низкая (документы не затрагиваются)

**Меры:**
- ✅ Миграции не трогают файлы документов
- ✅ Бэкап документов перед деплоем
- ✅ Документы хранятся в файловой системе, не в БД

### Риск 4: Проблемы с сессиями

**Вероятность:** Низкая (новая функциональность)

**Меры:**
- ✅ Если сессии не работают - можно использовать старый способ (через query параметр)
- ✅ Воркер не зависит от сессий

## Чеклист перед деплоем

- [ ] Сделан бэкап БД
- [ ] Сделан бэкап инженерных документов
- [ ] Сделан бэкап конфигурации
- [ ] Проверено что воркер работает
- [ ] Проверено количество данных в БД
- [ ] Проверено наличие инженерных документов
- [ ] Проверены переменные окружения
- [ ] Проверен доступ к БД
- [ ] Подготовлен план отката

## Чеклист после деплоя

- [ ] Миграции применены успешно
- [ ] Все данные сохранились (проверка COUNT)
- [ ] Инженерные документы на месте
- [ ] Веб-интерфейс запущен и доступен
- [ ] Воркер продолжает работать
- [ ] Можно добавить канал через веб-интерфейс
- [ ] Воркер подхватывает каналы из БД
- [ ] Нет ошибок в логах

## Рекомендуемый порядок действий

1. **Подготовка** (5-10 минут)
   - Бэкапы
   - Проверки

2. **Миграции БД** (2-5 минут)
   - Применить миграцию 001
   - Проверить данные
   - Применить миграцию 002

3. **Развёртывание веб-интерфейса** (10-15 минут)
   - Установка зависимостей
   - Настройка nginx/systemd
   - Тестовый запуск

4. **Проверка** (5 минут)
   - Проверка работы
   - Проверка данных
   - Тест функциональности

**Общее время:** ~30-40 минут

## Важные замечания

1. **Миграции безопасны** - они не удаляют данные, только добавляют поля
2. **Воркер не останавливается** - работает параллельно с деплоем
3. **Документы не затрагиваются** - они в файловой системе
4. **Можно откатить** - есть план отката и бэкапы
5. **Постепенный деплой** - можно тестировать на каждом этапе
