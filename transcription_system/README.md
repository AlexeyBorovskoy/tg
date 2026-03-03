# Transcription System (local)

Локальный независимый сервис транскрибации в визуальном стиле TG Digest.

## Что сделано
- Единый API-префикс: `/api/v1/transcription/*`
- Очередь задач транскрибации (SQLite)
- Поддержка ASR-провайдеров: `assemblyai`, `local_whisper`, `compare`, `mock`
- Глоссарий замен терминов (SQLite)
- LLM-постобработка transcript (OpenAI-compatible API, fallback по списку моделей)
- Chunked LLM-постобработка длинных transcript (без обрезки по лимиту символов)
- Библиотека промтов (SQLite): профили `voice`/`meeting`, default-промт по профилю
- Выгрузка артефактов: `md`, `txt`, `json`
- Разметка по ролям (diarization) + переименование спикеров
- Авто-назначение ролей спикеров (LLM + эвристический fallback)
- Word boost в ASR на основе терминов глоссария
- Meeting-протокол: `protocol.md/json` (тезисы, решения, поручения с цитатами)
- Сохранение сегментов стенограммы и статистики примененного глоссария в БД
- UI в стиле TG Digest: вкладки, карточки, таблицы, боковая оболочка
- Local-auth как в TG Digest: login/password, cookie-сессия, роли `admin/user`
- После входа открывается `/resources` (страница выбора ресурса)
- Изоляция задач по пользователю: обычный пользователь видит только свои jobs

## Запуск локально

```bash
cd /home/alexey/work/tg_didgest/transcription_system
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8081 --reload
```

Открыть:
- `http://127.0.0.1:8081/login`
- `http://127.0.0.1:8081/resources`
- `http://127.0.0.1:8081/`
- `http://127.0.0.1:8081/jobs`
- `http://127.0.0.1:8081/prompts`
- `http://127.0.0.1:8081/glossary`
- `http://127.0.0.1:8081/instructions`
- `http://127.0.0.1:8081/users` (только admin)

## Основные API
- `POST /api/v1/transcription/jobs` (multipart: `file`, `provider`, `diarization`, `speakers_expected`, `source_kind`, `llm_enabled`, `llm_profile`, `llm_model_override`, `roles_enabled`)
- `GET /api/v1/transcription/jobs`
- `GET /api/v1/transcription/jobs/{job_id}`
- `GET /api/v1/transcription/jobs/{job_id}/artifacts?format=md|txt|json`
- `GET /api/v1/transcription/jobs/{job_id}/artifacts?format=protocol_md|protocol_json`
- `GET /api/v1/transcription/jobs/{job_id}/artifacts?format=compare_md|compare_json|assemblyai_md|local_whisper_md|...`
- `GET /api/v1/transcription/jobs/{job_id}/segments`
- `GET /api/v1/transcription/jobs/{job_id}/glossary-stats`
- `GET /api/v1/transcription/jobs/{job_id}/protocol`
- `POST /api/v1/transcription/jobs/{job_id}/protocol/rebuild`
- `PUT /api/v1/transcription/jobs/{job_id}/speaker-map`
- `GET /api/v1/glossary`
- `POST /api/v1/glossary/terms`
- `DELETE /api/v1/glossary/terms/{id}`
- `GET /api/v1/prompts`
- `POST /api/v1/prompts`
- `PUT /api/v1/prompts/{id}`
- `DELETE /api/v1/prompts/{id}`
- `POST /api/v1/prompts/{id}/activate`
- `GET /api/v1/prompts/export?active_only=true|false` (скачать JSON библиотеки)
- `POST /api/v1/prompts/import` (загрузить JSON, mode=merge|replace, требуется `schema_version=1`)
- `GET /api/v1/admin/users` (только admin)

## Примечания
- Для локальной проверки без внешнего API выставлен `TRANSCRIPTION_PROVIDER=mock`.
- По умолчанию создается admin из env:
  - `ADMIN_LOGIN` (default `admin`)
  - `ADMIN_PASSWORD` (default `change_me`)
  - `AUTH_LOCAL_ENABLED=1`
  - `AUTH_SESSION_DAYS=14`
  - `RESOURCE_TG_DIGEST_URL` — ссылка на TG Digest в окне выбора ресурса.
- Для боевой транскрибации переключить на `TRANSCRIPTION_PROVIDER=assemblyai`, задать `ASSEMBLYAI_API_KEY` и при необходимости `ASSEMBLYAI_SPEECH_MODELS` (по умолчанию `universal-2`).
- Для локального ASR через Whisper: `TRANSCRIPTION_PROVIDER=local_whisper` и установить `faster-whisper` отдельно:
  `pip install faster-whisper`
  при необходимости настроить `WHISPER_*`.
- Для параллельного сравнения провайдеров: `provider=compare` в запросе задачи.
- Для LLM-постобработки используются те же переменные, что и в TG Digest: `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`.
- Совместимость сохранена: можно использовать старые имена `OPENAI_API_BASE` и `LLM_MAX_OUTPUT_TOKENS`; также поддерживается `OPENAI_MAX_TOKENS`.
- Приоритет загрузки настроек для LLM/ASR: env процесса -> `tg_digest_system/docker/secrets.env` -> `tg_digest_system/docker/.env` -> `transcription_system/.env`.
- Если `OPENAI_API_KEY` не задан, авто-назначение ролей продолжит работу в эвристическом режиме (`Спикер 1`, `Спикер 2`...).

## Логика выбора LLM модели (MVP)
- Сервис использует `OPENAI_MODEL_CANDIDATES` как приоритетную цепочку моделей (слева направо).
- Если у конкретной задачи передан `llm_model_override`, он проверяется первым.
- При ошибке модели (недоступна/неразрешена/ошибка API) сервис автоматически пробует следующую.
- Если ни одна модель не сработала, задача завершается успешно с ASR-текстом (`llm_status=fallback_error`).

## Качество и роли (MVP)
- Глоссарий применяется в двух местах: post-fix текста и `word_boost` при отправке задачи в AssemblyAI.
- Для длинных стенограмм LLM-постобработка выполняется chunk-by-chunk с overlap по строкам.
- Для ролей действует порядок:
  1. Если уже есть user-map, используется он.
  2. Иначе пробуется LLM-классификация ролей.
  3. При ошибке ставится стабильный fallback `Спикер N`.
- Для `source_kind=meeting` дополнительно формируется протокол:
  1. Ключевые тезисы
  2. Решения
  3. Поручения (задача, ответственный, срок, цитата)
