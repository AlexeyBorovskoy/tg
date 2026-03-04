# План деплоя: Transcription + TG Digest (единый вход)

## 1. Цель
- Развернуть `transcription_system` рядом с `tg_digest_system`.
- Использовать единый cookie/session контур (`session_token`, таблица `user_sessions`).
- Сохранить раздельные интерфейсы ролей `admin/user` в обоих сервисах.
- Удалять исходные аудиофайлы сразу после обработки.

## 2. Что уже реализовано локально
- В `transcription_system` добавлен shared-auth режим через PostgreSQL TG Digest:
  - чтение сессий из `user_sessions`;
  - вход по логин/пароль через `user_local_auth`;
  - создание/удаление сессии в общей таблице;
  - роль admin определяется по `AUTH_SHARED_ADMIN_LOGIN` (для `Alex`).
- Для задач транскрибации включена политика удаления исходного аудио:
  - `KEEP_UPLOADED_AUDIO=0` (по умолчанию);
  - удаление файла выполняется в `finally` независимо от результата задачи.

## 3. Конфигурация для прода

### Обязательные env (transcription web/worker)
- `AUTH_SHARED_ENABLED=1`
- `AUTH_SHARED_COOKIE_NAME=session_token`
- `AUTH_SHARED_ADMIN_LOGIN=Alex`
- `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` (та же БД, что у TG Digest auth)
- `AUTH_LOCAL_ENABLED=1` (включает login-flow)
- `KEEP_UPLOADED_AUDIO=0`

### Рекомендуемые ссылки
- `AUTH_SHARED_LOGIN_URL=http://89.124.65.229:8010/login`
- `AUTH_SHARED_REGISTER_URL=http://89.124.65.229:8010/register`
- `RESOURCE_TG_DIGEST_URL=http://89.124.65.229:8010/setup`

## 4. Проверка ресурсов сервера Нила (снимок)
Дата проверки: 2026-03-04 09:18 MSK
- CPU: 2 vCPU
- RAM: 3.8 GiB (used 2.2 GiB, available ~1.6 GiB)
- Disk: 79G total, 25G used (32%)
- Swap: отсутствует
- Нагрузка: `load average 0.66 / 0.74 / 1.85`
- Вывод: ресурсов достаточно для легкого web+worker контура при ограниченном параллелизме.

## 5. Нагрузка и ограничения
- Не разворачивать LLM локально на сервере.
- Использовать только внешние API:
  - ASR: AssemblyAI
  - LLM postprocess: OpenAI-compatible endpoint
- В worker ограничить параллелизм: 1 активная тяжелая задача.
- При необходимости добавить swap 2-4 GiB для защиты от OOM.

## 6. Порядок деплоя
1. Обновить код `transcription_system` на сервере.
2. Установить зависимости (`pip install -r requirements.txt`), включая `psycopg2-binary`.
3. Прописать env из секции 3.
4. Перезапустить web/worker `transcription_system`.
5. Smoke-check:
   - вход под `Alex` на transcription;
   - переход в TG Digest без повторного логина (общая сессия);
   - доступ к `/users` только у admin;
   - запуск test job и проверка удаления upload-файла после завершения.
6. Зафиксировать изменения в `/opt/server-docs/logbook.md`.

## 7. Критерии готовности
- Единый login/session работает между `:8010` и сервисом транскрибации.
- `Alex` распознается как admin в обеих системах.
- Для user нет доступа к admin-страницам.
- Исходное аудио отсутствует на диске после выполнения job.
- Логи/документация на сервере обновлены.
