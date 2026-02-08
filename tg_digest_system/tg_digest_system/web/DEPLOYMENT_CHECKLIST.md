# Чеклист безопасного деплоя

## Перед деплоем

### Бэкапы
- [ ] Бэкап БД: `pg_dump -U tg_digest -d tg_digest -F c -f backup_YYYYMMDD_HHMMSS.dump`
- [ ] Бэкап документов: `tar -czf docs_backup_YYYYMMDD_HHMMSS.tar.gz /home/ripas/tg_digest_system/docs/`
- [ ] Бэкап конфигурации: `cp channels.json channels.json.backup_YYYYMMDD_HHMMSS`

### Проверки
- [ ] Воркер работает: `systemctl status tg_digest_worker`
- [ ] Количество сообщений в БД: `SELECT COUNT(*) FROM tg.messages;`
- [ ] Инженерные документы на месте: `ls -la docs/reference/`
- [ ] Переменные окружения настроены: проверить `.env`
- [ ] Доступ к БД работает: `psql -U tg_digest -d tg_digest -c "SELECT 1;"`

## Деплой

### Этап 1: Миграции БД
- [ ] Проверить текущее состояние: `\d tg.messages` (есть ли user_id?)
- [ ] Применить миграцию 001: `psql -U tg_digest -d tg_digest -f 001_add_user_id.sql`
- [ ] Проверить что все данные получили user_id=1
- [ ] Применить миграцию 002: `psql -U tg_digest -d tg_digest -f 002_add_user_sessions.sql`
- [ ] Проверить что таблица user_sessions создана

### Этап 2: Веб-интерфейс
- [ ] Установить зависимости: `pip install -r requirements.txt`
- [ ] Скопировать файлы (или git pull)
- [ ] Настроить nginx (если нужно)
- [ ] Настроить systemd service
- [ ] Тестовый запуск: `python web_api.py`
- [ ] Проверить health: `curl http://localhost:8080/health`
- [ ] Запустить через systemd: `systemctl start tg_digest_web`

### Этап 3: Интеграция
- [ ] Проверить что воркер продолжает работать
- [ ] Проверить логи воркера на ошибки
- [ ] Проверить что воркер загружает каналы из БД

## После деплоя

### Проверки
- [ ] Веб-интерфейс доступен: `curl http://158.160.19.253/`
- [ ] Страница входа открывается
- [ ] Можно войти с Telegram ID
- [ ] Можно добавить канал через веб-интерфейс
- [ ] Канал появился в списке
- [ ] Воркер подхватил канал из БД
- [ ] Данные не потеряны: `SELECT COUNT(*) FROM tg.messages WHERE user_id = 1;`
- [ ] Инженерные документы на месте: `ls -la docs/reference/`
- [ ] Можно скачать документ через веб-интерфейс

### Логи
- [ ] Нет ошибок в логах веб-интерфейса: `journalctl -u tg_digest_web -n 50`
- [ ] Нет ошибок в логах воркера: `journalctl -u tg_digest_worker -n 50`

## Откат (если нужно)

- [ ] Остановить веб-интерфейс: `systemctl stop tg_digest_web`
- [ ] Восстановить БД из бэкапа (если нужно)
- [ ] Восстановить документы из бэкапа (если нужно)
- [ ] Проверить что воркер работает
