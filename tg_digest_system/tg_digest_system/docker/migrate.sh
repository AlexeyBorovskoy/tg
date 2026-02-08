#!/bin/sh
# Запуск миграций БД (001–005). Вызывается из контейнера migrate.
set -e
MIGRATIONS_DIR="${MIGRATIONS_DIR:-/migrations}"
for f in 001_add_user_id.sql 002_add_user_sessions.sql 003_add_prompt_texts.sql 004_add_prompts_table.sql 005_prompt_library.sql 006_entity_settings_and_bots.sql; do
  [ -f "${MIGRATIONS_DIR}/${f}" ] || continue
  echo "Running migration: $f"
  psql -v ON_ERROR_STOP=1 -f "${MIGRATIONS_DIR}/${f}"
done
echo "Migrations done."
