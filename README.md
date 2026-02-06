# TG Digest System — Telegram → PostgreSQL → OCR → LLM

## 1. Назначение

Система автоматического мониторинга Telegram-каналов с генерацией управленческих дайджестов.

**Что делает:**
1. Читает новые сообщения из указанных Telegram-каналов
2. Распознаёт текст на изображениях (OCR — Tesseract)
3. Формирует дайджест с помощью ChatGPT по специальному промпту
4. Отправляет дайджест указанным получателям в Telegram

**Для кого:** руководители команд, инженеры, менеджеры проектов.

---

## 2. Архитектура

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Telegram       │────▶│  PostgreSQL     │────▶│  OpenAI GPT     │
│  каналы         │     │  (хранение)     │     │  (анализ)       │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                                               │
        ▼                                               ▼
┌─────────────────┐                             ┌─────────────────┐
│  OCR            │                             │  Дайджест →     │
│  (Tesseract)    │                             │  Telegram Bot   │
└─────────────────┘                             └─────────────────┘
```

---

## 3. Структура репозитория

```
analysis-methodology/
├── tg_digest_system/           # 🔥 ОСНОВНАЯ СИСТЕМА (Python)
│   ├── tg_digest_system/
│   │   ├── scripts/            # Основной код
│   │   │   ├── digest_worker.py    # Главный воркер
│   │   │   ├── telegram_client.py  # Работа с Telegram
│   │   │   ├── llm.py              # Интеграция OpenAI
│   │   │   ├── ocr.py              # OCR (Tesseract)
│   │   │   ├── database.py         # PostgreSQL
│   │   │   └── config.py           # Конфигурация
│   │   ├── config/             # JSON-конфиги
│   │   ├── prompts/            # Промпты для LLM
│   │   ├── db/                 # Схема БД, миграции
│   │   └── docker/             # Docker Compose
│   ├── prompts/                # Промпты (копия для удобства)
│   └── docs/                   # Справочная документация
│
├── config/                     # YAML-шаблоны конфигурации
│   ├── llm.yaml.example
│   ├── database.yaml.example
│   └── ...
│
├── deploy/                     # Скрипты деплоя на Yandex Cloud
│   ├── deploy_yandex.sh
│   ├── tg_digest_worker.service
│   └── vpn.conf                # Настройка SOCKS-прокси
│
├── scripts/                    # [Legacy] Старые shell-скрипты
├── database/                   # Документация по БД
└── docs/                       # Старые дайджесты и документация
```

---

## 4. Быстрый старт

### Требования

- Ubuntu 22.04+
- Python 3.10+
- PostgreSQL 15+
- Tesseract OCR
- Доступ к OpenAI API (через VPN/прокси из РФ)

### Установка

```bash
cd tg_digest_system/tg_digest_system

# Виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Конфигурация
cp ../../config/llm.yaml.example ../../config/llm.yaml
# Заполнить API ключи

# Запуск
python scripts/digest_worker.py
```

### Docker

```bash
cd tg_digest_system/tg_digest_system/docker
docker-compose up -d
```

---

## 5. Конфигурация

### Переменные окружения (.env)

```bash
# Telegram API
TG_API_ID=12345678
TG_API_HASH=abcdef1234567890
TG_BOT_TOKEN=123:ABC...

# PostgreSQL
PGHOST=localhost
PGPORT=5432
PGDATABASE=tg_digest
PGUSER=tg_digest
PGPASSWORD=secret

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

### Каналы (config/channels.json)

```json
{
  "channels": [
    {
      "id": -1002700886173,
      "name": "Основной канал",
      "enabled": true,
      "prompt_file": "prompts/digest_management.md",
      "poll_interval_minutes": 30,
      "recipients": [
        {"telegram_id": 123456789, "name": "Иванов", "send_file": true}
      ]
    }
  ]
}
```

---

## 6. База данных

| Схема | Таблица | Назначение |
|-------|---------|------------|
| `tg` | `messages` | Сообщения Telegram |
| `tg` | `media` | Медиафайлы |
| `tg` | `media_text` | OCR-результаты |
| `rpt` | `report_state` | Курсоры обработки |
| `rpt` | `digests` | Сгенерированные дайджесты |
| `rpt` | `deliveries` | Статусы доставки |

---

## 7. Деплой на Yandex Cloud

```bash
# Подготовка сервера
./deploy/deploy_yandex.sh

# Финализация (после ручных настроек)
./deploy/finish_deploy_yandex.sh

# Проверка здоровья
./deploy/healthcheck_yandex.sh
```

### Настройка прокси для OpenAI

Из РФ прямой доступ к OpenAI заблокирован. Используется SOCKS5-туннель:

```bash
# SSH-туннель на VPS
ssh -D 1080 -f -N user@vps-server

# В systemd-сервисе
Environment=HTTPS_PROXY=socks5://127.0.0.1:1080
```

---

## 8. Systemd-сервис

```ini
[Unit]
Description=TG Digest Worker
After=network.target postgresql.service

[Service]
Type=simple
User=ripas
WorkingDirectory=/home/ripas/tg_digest_system
EnvironmentFile=/home/ripas/tg_digest_system/tg_digest_system/.env
ExecStart=/home/ripas/tg_digest_system/.venv/bin/python3 scripts/digest_worker.py
Restart=always

[Install]
WantedBy=multi-user.target
```

---

## 9. Продакшен-инстанс

**Сервер:** Yandex Cloud VM (`158.160.19.253`)

```bash
# Подключение
ssh ripas@158.160.19.253

# Статус сервиса
sudo systemctl status tg_digest_worker

# Логи
sudo journalctl -u tg_digest_worker -f

# Перезапуск
sudo systemctl restart tg_digest_worker
```

---

## 10. Документация

- [Руководство по развёртыванию](tg_digest_system/tg_digest_system/docs/README.md)
- [Описание БД](database/)
- [Промпты для LLM](tg_digest_system/prompts/)

---

## 11. Legacy

Папка `scripts/` содержит старую реализацию на shell (GigaChat, poll_tg_db.sh).
Для новых задач используйте `tg_digest_system/`.
