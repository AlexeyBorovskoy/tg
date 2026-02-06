# Референс стенда: Telegram → PostgreSQL → Docs-as-Code (GitLab)

## 1. Идентификация источника
Целевой чат:
- peer_type: channel
- peer_id: 2700886173

Исключения:
- peer_id = -1 используется для тестовых вставок в БД и в аналитике должен быть исключён фильтром по целевому peer.

## 2. PostgreSQL (Yandex VM)
Версия PostgreSQL: 16.11 (Ubuntu 24.04).
Расширение:
- pgvector: 0.6.0 (установлено).
- postgis: на момент проверки не установлено (в ext list возвращался только vector).

Схемы:
- public
- rag
- rpt
- tg

Таблица сообщений:
- tg.messages: 9 полей (см. database/01_tg_messages.md)

Статистика tg.messages (на момент проверки):
- msg_count: 451
- msg_text_count: 408
- диапазон dt: 2025-05-23 … 2026-01-28
- max msg_id по целевому peer: 1241

## 3. Курсоры обработки
Таблицы:
- rpt.report_state
- rpt.llm_report_state

Синхронизация курсоров по целевому peer выполнена:
- last_msg_id = 1241
- контроль new_cnt = 0

## 4. Smoke-test артефакт
Файл smoke-test digest:
- docs/digests/2026-01-28/slot_manual/digest_smoketest_20260128T141646Z.md

Назначение smoke-test:
- подтвердить корректность выборки из БД;
- подтвердить создание Markdown-артефакта перед включением LLM и Telegram доставки.

## 5. GitLab доступ (SSH)
GitLab:
- host: gitlab.ripas.ru
- SSH port: 8611
- repo: ssh://git@gitlab-ripas-8611/analyzer/analysis-methodology.git

SSH alias (рекомендуемый):
- Host: gitlab-ripas-8611
- IdentityFile: /home/ripas/.ssh/gitlab_tg_rag_reports
