#!/usr/bin/env bash
# ==============================================================================
# Полный деплой TG Digest в Yandex Cloud с локальной машины.
# Требует: установленный и настроенный yc (yc init), SSH-доступ к ВМ.
#
# Использование:
#   cd tg_digest_system/tg_digest_system/docker/yandex
#   ./deploy-yandex-full.sh
#
# Или из каталога docker:
#   ./yandex/deploy-yandex-full.sh
#
# Переменные (опционально):
#   YC_FOLDER_ID   — ID каталога (по умолчанию из yc config)
#   YC_ZONE        — зона (по умолчанию ru-central1-a)
#   VM_NAME        — имя ВМ (по умолчанию tg-digest)
#   SSH_USER       — пользователь SSH (по умолчанию ubuntu)
#   REPO_URL       — URL репо для клонирования на ВМ (если не копируем с локальной машины)
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPO_TOP="$(cd "$SCRIPT_DIR/../../.." && pwd)"

VM_NAME="${VM_NAME:-tg-digest}"
YC_ZONE="${YC_ZONE:-ru-central1-a}"
SSH_USER="${SSH_USER:-ubuntu}"
REMOTE_DIR="/home/$SSH_USER/tg_digest_system"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o ServerAliveInterval=5"

# Путь к yc (если установлен в /tmp/yc-install или задан YC_BIN)
YC_BIN="${YC_BIN:-}"
if [[ -z "$YC_BIN" ]] && [[ -x /tmp/yc-install/bin/yc ]]; then
  YC_BIN=/tmp/yc-install/bin/yc
fi
[[ -n "$YC_BIN" ]] && export PATH="$(dirname "$YC_BIN"):$PATH"

echo "=== Деплой TG Digest в Yandex Cloud ==="
echo ""

# Проверка yc
if ! command -v yc &>/dev/null; then
  echo "Yandex Cloud CLI (yc) не найден в PATH."
  echo "Установите: curl -sSL https://storage.yandexcloud.net/yandexcloud-yc/install.sh | bash -s -- -i /tmp/yc-install -n"
  echo "Затем: export PATH=/tmp/yc-install/bin:\$PATH && yc init"
  echo "Или задайте путь: YC_BIN=/path/to/yc ./deploy-yandex-full.sh"
  exit 1
fi

if ! yc config list &>/dev/null; then
  echo "yc не настроен. Выполните: yc init"
  exit 1
fi

FOLDER_ID=$(yc config get folder-id 2>/dev/null || true)
if [[ -z "$FOLDER_ID" ]]; then
  echo "Задайте folder-id: yc config set folder-id <FOLDER_ID>"
  exit 1
fi
echo "Каталог Yandex Cloud: $FOLDER_ID"
echo "Зона: $YC_ZONE"
echo "Имя ВМ: $VM_NAME"
echo ""

# Сеть и подсеть
echo "=== 1. Сеть ==="
yc vpc network create --name net-tg-digest 2>/dev/null || true
yc vpc subnet create --name subnet-tg-digest --network-name net-tg-digest --zone "$YC_ZONE" --range 10.0.0.0/24 2>/dev/null || true
echo "Готово."
echo ""

# Группа безопасности: порты 22 (SSH) и 8000 (веб) из интернета
echo "=== 1b. Группа безопасности (порты 22, 8000) ==="
SG_ID=$(yc vpc network get net-tg-digest --format json 2>/dev/null | jq -r '.default_security_group_id // empty' 2>/dev/null) || true
if [[ -n "$SG_ID" ]]; then
  for port in 22 8000; do
    yc vpc security-group update-rules "$SG_ID" --add-rule "direction=ingress,port=$port,protocol=tcp,v4-cidrs=[0.0.0.0/0]" 2>/dev/null && echo "Порт $port открыт." || true
  done
else
  echo "Группу безопасности откройте вручную: Сеть → Группы безопасности → правило входящий TCP 22 и 8000, источник 0.0.0.0/0"
fi
echo "Готово."
echo ""

# ВМ
echo "=== 2. Создание ВМ ==="
SSH_KEY_FILE=""
for f in ~/.ssh/id_ed25519.pub ~/.ssh/id_rsa.pub; do
  [[ -r "$f" ]] && SSH_KEY_FILE="$f" && break
done
if yc compute instance get "$VM_NAME" &>/dev/null 2>&1; then
  echo "ВМ $VM_NAME уже существует. Используем её."
else
  # Интерфейс: подсеть + публичный IP + группа безопасности (порты 22, 8000)
  NET_IF="subnet-name=subnet-tg-digest,nat-ip-version=ipv4"
  [[ -n "$SG_ID" ]] && NET_IF="$NET_IF,security-group-ids=$SG_ID"
  CREATE_OPTS=(
    --name "$VM_NAME"
    --zone "$YC_ZONE"
    --platform standard-v3
    --cores 2
    --memory 4G
    --create-boot-disk "image-family=ubuntu-2204-lts,image-folder-id=standard-images,size=20G"
    --network-interface "$NET_IF"
    --metadata serial-port-enable=1
    --serial-port-settings ssh-authorization=instance_metadata
  )
  if [[ -n "$SSH_KEY_FILE" ]]; then
    CREATE_OPTS+=(--ssh-key "$SSH_KEY_FILE")
    SSH_USER="yc-user"
    REMOTE_DIR="/home/yc-user/tg_digest_system"
    echo "SSH-ключ: $SSH_KEY_FILE (пользователь на ВМ: yc-user)"
  fi
  yc compute instance create "${CREATE_OPTS[@]}"
  echo "ВМ создана."
fi
echo ""

# Публичный IP (из one_to_one_nat; первый "address" в JSON — приватный)
echo "=== 3. Ожидание публичного IP и SSH ==="
PUBLIC_IP=""
for i in {1..30}; do
  JSON=$(yc compute instance get "$VM_NAME" --format json 2>/dev/null) || true
  if command -v jq &>/dev/null; then
    PUBLIC_IP=$(echo "$JSON" | jq -r '.network_interfaces[0].primary_v4_address.one_to_one_nat.address // empty' 2>/dev/null)
  fi
  if [[ -z "$PUBLIC_IP" ]]; then
    # Публичный IP — тот, что внутри one_to_one_nat (в JSON идёт после "one_to_one_nat")
    PUBLIC_IP=$(echo "$JSON" | grep -oE '"one_to_one_nat"[^}]*"address"[^"]*"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+"' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1)
  fi
  if [[ -z "$PUBLIC_IP" ]]; then
    for ip in $(echo "$JSON" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+'); do
      if [[ ! "$ip" =~ ^10\. ]]; then
        PUBLIC_IP="$ip"
        break
      fi
    done
  fi
  [[ -n "$PUBLIC_IP" ]] && break
  echo "Ожидание IP... ($i/30)"
  sleep 10
done
if [[ -z "$PUBLIC_IP" ]]; then
  echo "Не удалось получить публичный IP. Проверьте ВМ в консоли Yandex Cloud."
  exit 1
fi
echo "Публичный IP: $PUBLIC_IP"
echo "Ожидание доступности SSH (до 2 минут)..."
for i in {1..24}; do
  if ssh $SSH_OPTS -o ConnectTimeout=5 "${SSH_USER}@${PUBLIC_IP}" "echo ok" 2>/dev/null; then
    echo "SSH доступен."
    break
  fi
  [[ $i -eq 24 ]] && { echo "SSH недоступен. Проверьте группу безопасности (порт 22) и попробуйте позже: ssh ${SSH_USER}@${PUBLIC_IP}"; exit 1; }
  sleep 5
done
echo ""

# Установка Docker на ВМ (если ещё нет)
echo "=== 4. Docker на ВМ ==="
ssh $SSH_OPTS "${SSH_USER}@${PUBLIC_IP}" "command -v docker &>/dev/null || (curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $SSH_USER)"
echo "Готово."
echo ""

# Копирование кода на ВМ
echo "=== 5. Копирование проекта на ВМ ==="
ssh $SSH_OPTS "${SSH_USER}@${PUBLIC_IP}" "mkdir -p $REMOTE_DIR"
SRC_DIR="$DOCKER_DIR/.."
if command -v rsync &>/dev/null; then
  rsync -avz -e "ssh $SSH_OPTS" \
    --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' --exclude 'node_modules' \
    "$SRC_DIR/" "${SSH_USER}@${PUBLIC_IP}:${REMOTE_DIR}/"
else
  (cd "$SRC_DIR" && tar cf - .) | ssh $SSH_OPTS "${SSH_USER}@${PUBLIC_IP}" "cd $REMOTE_DIR && tar xf -"
fi
echo "Код скопирован."
echo ""

# Запуск установки и деплоя на ВМ (код скопирован как SRC_DIR/ -> REMOTE_DIR/, т.е. REMOTE_DIR/$(basename DOCKER_DIR)/docker)
REMOTE_DOCKER_DIR="$REMOTE_DIR/$(basename "$DOCKER_DIR")/docker"
echo "=== 6. Установка и запуск на ВМ ==="
ssh $SSH_OPTS "${SSH_USER}@${PUBLIC_IP}" "cd $REMOTE_DOCKER_DIR && chmod +x deploy.sh yandex/setup-on-vm.sh 2>/dev/null; bash yandex/setup-on-vm.sh"
echo ""

echo "=== Готово ==="
echo ""
echo "Вход в систему:  http://${PUBLIC_IP}:8000/login"
echo "Главная (после входа):  http://${PUBLIC_IP}:8000/"
echo ""
echo "Откройте в Yandex Cloud порт 8000 для ВМ (Сеть → Группы безопасности → входящий TCP 8000)."
echo "Проверка: curl -s http://${PUBLIC_IP}:8000/health"
