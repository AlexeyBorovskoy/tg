#!/usr/bin/env bash
# ==============================================================================
# Завершение деплоя TG Digest на Yandex VM (запускать НА СЕРВЕРЕ под ripas)
# Вызов: bash finish_deploy_yandex.sh [--skip-env-check] [--run-once]
# ==============================================================================

set -e
DEPLOY=~/tg_digest_deploy
SKIP_ENV_CHECK=
RUN_ONCE=

for x in "$@"; do
  case "$x" in
    --skip-env-check) SKIP_ENV_CHECK=1 ;;
    --run-once)       RUN_ONCE=1 ;;
  esac
done

echo "=== 1. Остановка старого сбора (tg_ingest / cron) ==="
# Закомментировать задание cron вручную: crontab -e
if crontab -l 2>/dev/null | grep -q "tg_ingest\|run_export"; then
  echo "ВНИМАНИЕ: в crontab есть задание tg_ingest/run_export. Закомментируйте его: crontab -e"
fi
pkill -f "tg_poll.py" 2>/dev/null || true
pkill -f "run_export" 2>/dev/null || true
echo "Готово."

echo ""
echo "=== 2. Проверка .env (TG_BOT_TOKEN, OPENAI_API_KEY) ==="
if [[ -z "$SKIP_ENV_CHECK" ]]; then
  missing=
  source "$DEPLOY/.env" 2>/dev/null || true
  if [[ -z "${TG_BOT_TOKEN:-}" ]] || [[ "$TG_BOT_TOKEN" =~ ^[[:space:]]*$ ]]; then
    missing="TG_BOT_TOKEN"
  fi
  if [[ -z "${OPENAI_API_KEY:-}" ]] || [[ "$OPENAI_API_KEY" =~ ^[[:space:]]*$ ]]; then
    [[ -n "$missing" ]] && missing="$missing, "
    missing="${missing}OPENAI_API_KEY"
  fi
  if [[ -n "$missing" ]]; then
    echo "Ошибка: в ~/tg_digest_deploy/.env не заданы: $missing"
    echo "Добавьте их и запустите снова или используйте --skip-env-check."
    exit 1
  fi
  echo "Ключи заданы."
else
  echo "Проверка пропущена (--skip-env-check)."
fi

echo ""
echo "=== 3. Установка systemd-сервиса ==="
sudo cp "$DEPLOY/deploy/tg_digest_worker.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tg_digest_worker
echo "Сервис включён."

echo ""
echo "=== 4. Запуск/перезапуск сервиса ==="
sudo systemctl restart tg_digest_worker
sudo systemctl status tg_digest_worker --no-pager || true

if [[ -n "$RUN_ONCE" ]]; then
  echo ""
  echo "=== 5. Один запуск воркера (--once) ==="
  cd "$DEPLOY"
  set -a && source .env && set +a
  source .venv/bin/activate
  export PYTHONPATH="$DEPLOY"
  python scripts/digest_worker.py --once || true
  echo "Готово. Сервис продолжает работать по расписанию (interval 60)."
fi

echo ""
echo "Готово. Логи: journalctl -u tg_digest_worker -f"
