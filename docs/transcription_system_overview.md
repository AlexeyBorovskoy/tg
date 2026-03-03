# Transcription System Overview

`transcription_system` — независимая подсистема транскрибации аудио, интегрируемая с TG Digest на уровне единого входа и единого контура LLM-настроек.

## Назначение

- Преобразование голосовых сообщений и аудио совещаний в текст.
- Постобработка текста LLM с управляемыми промптами.
- Распределение по ролям спикеров и формирование meeting-протокола.

## Основные компоненты

- `app/main.py`: FastAPI API, страницы UI, local-auth и маршруты.
- `app/transcribe.py`: интеграция ASR-провайдеров.
- `app/llm_postprocess.py`: postprocessing через OpenAI-compatible endpoint.
- `app/role_assignment.py`: авто-назначение ролей спикеров.
- `app/protocol_builder.py`: генерация протокола совещания.
- `app/db.py`: SQLite-хранилище задач, промптов, глоссария, сессий.

## Провайдеры и режимы

- `assemblyai`: основной облачный ASR.
- `local_whisper`: локальный ASR.
- `compare`: параллельный прогон и сравнительный отчет.
- `mock`: тестовый режим без внешних вызовов.

## Артефакты

Для каждой задачи:
- `md`, `txt`, `json`.

Для `source_kind=meeting` дополнительно:
- `protocol_md`, `protocol_json`.

Для `provider=compare` дополнительно:
- `compare_md`, `compare_json`.

## Авторизация

- local-auth: `login/password`;
- роли: `admin` и `user`;
- фильтрация задач по владельцу;
- страница выбора ресурса: `/resources`.

## Конфигурация

Поддерживается повторное использование ключей из TG Digest (`OPENAI_*`, `ASSEMBLYAI_*`) через общий механизм загрузки окружения.

## Запуск

Подробные команды и переменные: `transcription_system/README.md`.
