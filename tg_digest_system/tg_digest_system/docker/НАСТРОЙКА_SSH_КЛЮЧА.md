# Настройка SSH ключа для туннеля

## Шаг 1: Создать SSH ключ на Yandex Cloud сервере

Выполните на сервере Yandex Cloud (93.77.185.71):

```bash
# Создать SSH ключ (если еще нет)
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_vps -N ""

# Показать публичный ключ
cat ~/.ssh/id_ed25519_vps.pub
```

Скопируйте публичный ключ (начинается с `ssh-ed25519 ...`).

## Шаг 2: Добавить ключ на VPS сервер

### Вариант A: Если у вас есть доступ к VPS по SSH

Подключитесь к VPS (45.95.2.49) и выполните:

```bash
# Добавить публичный ключ в authorized_keys
mkdir -p ~/.ssh
chmod 700 ~/.ssh
echo "ВАШ_ПУБЛИЧНЫЙ_КЛЮЧ_ИЗ_ШАГА_1" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

### Вариант B: Через веб-интерфейс VPS провайдера

1. Войдите в панель управления VPS (weasel)
2. Найдите раздел "SSH ключи" или "Авторизация"
3. Добавьте публичный ключ из шага 1

### Вариант C: Использовать ssh-copy-id (если есть пароль)

```bash
# С Yandex Cloud сервера
ssh-copy-id -i ~/.ssh/id_ed25519_vps.pub root@45.95.2.49
# Или если другой пользователь:
ssh-copy-id -i ~/.ssh/id_ed25519_vps.pub username@45.95.2.49
```

## Шаг 3: Проверить доступ

```bash
# С Yandex Cloud сервера
ssh -i ~/.ssh/id_ed25519_vps root@45.95.2.49 "echo 'Подключение успешно'"
```

## Шаг 4: Настроить SSH config (опционально)

Создайте файл `~/.ssh/config` на Yandex Cloud сервере:

```bash
cat > ~/.ssh/config << 'EOF'
Host vps-tunnel
    HostName 45.95.2.49
    User root
    IdentityFile ~/.ssh/id_ed25519_vps
    StrictHostKeyChecking no
    ServerAliveInterval 60
    ServerAliveCountMax 3
EOF

chmod 600 ~/.ssh/config
```

Теперь можно использовать: `ssh vps-tunnel` вместо `ssh root@45.95.2.49`

## Шаг 5: Запустить туннель

После настройки ключа:

```bash
cd /home/yc-user/tg_digest_system/tg_digest_system/docker
./setup-tunnel.sh
```

Или вручную:

```bash
ssh -D 1080 -f -N -i ~/.ssh/id_ed25519_vps root@45.95.2.49
```

## Проверка туннеля

```bash
# Проверить, что порт 1080 слушается
ss -tlnp | grep 1080

# Проверить процессы
ps aux | grep 'ssh -D 1080' | grep -v grep
```
