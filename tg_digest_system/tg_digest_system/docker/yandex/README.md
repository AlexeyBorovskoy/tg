# Деплой TG Digest System в Yandex Cloud

Развёртывание полного стека (PostgreSQL + Web + Worker) на ВМ в Yandex Cloud с **авторизацией** (вход через Яндекс).

---

## Самый простой способ

**Вариант А: Новая ВМ (один скрипт)** — нужны Yandex Cloud CLI (`yc init`) и SSH-ключ:

```bash
cd tg_digest_system/tg_digest_system/docker/yandex
chmod +x deploy-yandex-full.sh
./deploy-yandex-full.sh
```

Скрипт создаст ВМ (если нет), установит Docker, скопирует код и запустит деплой. В конце выведет ссылку входа. Если пользователь на ВМ — **yc-user**, задайте: `SSH_USER=yc-user ./deploy-yandex-full.sh`.

**Вариант Б: ВМ уже есть, известен IP** — копируем код и деплоим одной командой. Запускать из каталога **docker** (родитель каталога yandex):

```bash
cd /path/to/tg_digest_system/tg_digest_system/docker
chmod +x yandex/deploy-to-existing-vm.sh
PUBLIC_IP=93.77.185.71 ./yandex/deploy-to-existing-vm.sh
```

Если вы уже в каталоге `docker/yandex`, перейдите на уровень выше и запустите скрипт:

```bash
cd ..
PUBLIC_IP=93.77.185.71 ./yandex/deploy-to-existing-vm.sh
```

Подставьте свой IP вместо `93.77.185.71`. Если на ВМ пользователь **yc-user**: `SSH_USER=yc-user PUBLIC_IP=... ./yandex/deploy-to-existing-vm.sh`.

**После первого запуска (любой вариант):** чтобы появилась кнопка «Войти через Яндекс», один раз зайдите на ВМ по SSH и добавьте в `secrets.env` данные OAuth и JWT:

```bash
ssh ubuntu@93.77.185.71   # или yc-user@...
cd /home/ubuntu/tg_digest_system/docker   # или /home/yc-user/tg_digest_system/docker
nano secrets.env
```

Добавьте или раскомментируйте строки (без пробела вокруг `=`):

```
AUTH_OWN_ENABLED=1
YANDEX_OAUTH_CLIENT_ID=ваш_client_id_из_кабинета_яндекс_oauth
YANDEX_OAUTH_CLIENT_SECRET=ваш_client_secret
BASE_URL=http://93.77.185.71:8000
JWT_SECRET=любая_длинная_случайная_строка
```

Сохраните файл и перезапустите web:

```bash
docker compose up -d --force-recreate web
```

После этого откройте в браузере `http://93.77.185.71:8000/login` — должна появиться кнопка «Войти через Яндекс».

---

## БД на Yandex: ваша существующая PostgreSQL не затрагивается

По умолчанию деплой **не использует** вашу уже работающую БД (Managed PostgreSQL или PostgreSQL на другой ВМ). На новой ВМ **tg-digest** поднимается **свой** PostgreSQL в Docker-контейнере с отдельным томом. Ваша текущая БД на Yandex **не пострадает** и не изменяется. Если нужно использовать именно вашу существующую БД вместо контейнерной — см. раздел «Деплой с существующими данными» в `docker/DEPLOY.md` (PGHOST, `docker-compose.existing.yml`).

## Автоматический деплой с локальной машины (один скрипт)

Если у вас установлен и настроен **Yandex Cloud CLI** (`yc init`) и есть SSH-ключ для доступа к ВМ:

```bash
cd tg_digest_system/tg_digest_system/docker/yandex
chmod +x deploy-yandex-full.sh
./deploy-yandex-full.sh
```

Скрипт **deploy-yandex-full.sh**:
1. Создаёт сеть и подсеть (если нет)
2. Создаёт ВМ **tg-digest** (Ubuntu 22.04, 2 vCPU, 4 ГБ RAM, 20 ГБ диск, публичный IP)
3. Ждёт появления публичного IP и доступности SSH
4. Устанавливает Docker на ВМ (если ещё нет)
5. Копирует проект на ВМ (rsync или tar)
6. Запускает **setup-on-vm.sh** на ВМ (создание .env/secrets.env, запуск deploy.sh --build)

В конце выведет ссылку для входа: **http://<ПУБЛИЧНЫЙ_IP>:8000/login**.  
Скрипт сам открывает порты **22** (SSH) и **8000** в группе безопасности сети. Если SSH всё равно недоступен: ВМ могла быть создана без вашего SSH-ключа — удалите ВМ и запустите скрипт снова (новая ВМ получит ключ из `~/.ssh/id_ed25519.pub` или `id_rsa.pub`, пользователь на ВМ: **yc-user**), либо добавьте ключ через Serial Console в консоли Yandex Cloud.

---

## Что нужно

- Аккаунт Yandex Cloud
- Yandex Cloud CLI (`yc`) установлен и настроен (`yc init`)
- SSH-ключ для доступа к ВМ (добавлен в профиль Yandex Cloud или в ~/.ssh)
- Секреты: PGPASSWORD, TG_API_ID, TG_API_HASH, OPENAI_API_KEY, при необходимости YANDEX_OAUTH_*, JWT_SECRET

## 1. Создание ВМ в Yandex Cloud

### Через консоль

1. [Yandex Cloud Console](https://console.cloud.yandex.ru) → Compute Cloud → Создать ВМ.
2. Образ: **Ubuntu 22.04**.
3. Платформа: Intel Broadwell или новее, 2 vCPU, 4 ГБ RAM (минимум).
4. Диск: 20 ГБ и больше.
5. Сеть: выдать **публичный IP**.
6. Метаданные: при необходимости добавьте SSH-ключ (или используйте существующий ключ из профиля).
7. Создать ВМ и записать **публичный IP**.

### Через CLI (yc)

```bash
# Папка и сеть (создайте при необходимости)
yc config set folder-id <FOLDER_ID>
yc vpc network create --name net-tg-digest 2>/dev/null || true
yc vpc subnet create --name subnet-tg-digest --network-name net-tg-digest --zone ru-central1-a --range 10.0.0.0/24 2>/dev/null || true

# ВМ
yc compute instance create \
  --name tg-digest \
  --zone ru-central1-a \
  --platform standard-v3 \
  --cores 2 --memory 4G \
  --create-boot-disk image-family=ubuntu-2204-lts,image-folder-id=standard-images,size=20G \
  --network-interface subnet-name=subnet-tg-digest,nat-ip-version=ipv4 \
  --metadata-from-file user-data=docker-install.yaml
```

Файл `docker-install.yaml` (user-data для облачной инициализации) можно взять из `user-data-docker.yaml` в этой папке.

Либо создайте ВМ без user-data и установите Docker вручную (шаг 2).

## 2. Подключение к ВМ и установка Docker

```bash
ssh ubuntu@<ПУБЛИЧНЫЙ_IP>
# или: ssh <ВАШ_ПОЛЬЗОВАТЕЛЬ>@<ПУБЛИЧНЫЙ_IP>
```

На ВМ:

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose git
sudo usermod -aG docker $USER
# Выйти и зайти снова, чтобы группа docker применилась
```

Или используйте скрипт из репозитория:

```bash
curl -sSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

## 3. Клонирование репозитория и подготовка конфигурации

На ВМ (после повторного входа по SSH):

```bash
# Путь, куда кладём проект (можно изменить)
export APP_DIR=~/tg_digest_system
mkdir -p "$APP_DIR"
cd "$APP_DIR"

# Клонируйте ваш репозиторий (замените на свой URL и ветку)
git clone --depth 1 https://github.com/AlexeyBorovskoy/tg.git repo
cd repo
# Если проект в подпапке (например tg_digest_system внутри репо):
# cd tg_digest_system/tg_digest_system/docker
# Иначе найдите каталог docker в вашем дереве и перейдите в него
cd tg_digest_system/tg_digest_system/docker
```

Узнайте публичный IP ВМ (если не знаете):

```bash
curl -s ifconfig.me
# или: yc compute instance get tg-digest --format json | jq -r '.network_interfaces[0].primary_v4_address.one_to_one_nat.address'
```

Создайте `.env` и `secrets.env`:

```bash
cp .env.example .env
cp secrets.env.example secrets.env

# Отредактируйте .env: PGPASSWORD, WEB_PORT=8000
# Отредактируйте secrets.env:
#   PGPASSWORD=...
#   TG_API_ID=... TG_API_HASH=... TG_BOT_TOKEN=...
#   OPENAI_API_KEY=...
#   AUTH_OWN_ENABLED=1
#   BASE_URL=http://<ПУБЛИЧНЫЙ_IP>:8000
#   JWT_SECRET=<длинная_случайная_строка>
# При использовании OAuth: YANDEX_OAUTH_CLIENT_ID, YANDEX_OAUTH_CLIENT_SECRET
nano .env
nano secrets.env
```

**Важно:** в `BASE_URL` укажите реальный адрес, по которому вы заходите в систему (например `http://<ПУБЛИЧНЫЙ_IP>:8000` или `https://ваш-домен.ru`). От этого зависят ссылки OAuth (redirect_uri).

## 4. Запуск деплоя на ВМ

В каталоге `docker` на ВМ:

```bash
chmod +x deploy.sh
./deploy.sh --build
```

Проверка:

```bash
docker-compose ps
curl -s http://localhost:8000/health
```

## 5. Доступ из интернета

- Порт **8000** должен быть открыт в **Security Groups** / файрволе Yandex Cloud для ВМ.
- В консоли: Сеть → Группы безопасности → группа ВМ → Добавить правило: входящий TCP 8000, источник 0.0.0.0/0 (или ограничьте по IP).

После этого откройте в браузере:

- **http://<ПУБЛИЧНЫЙ_IP>:8000** — главная; при включённой авторизации будет редирект на **/login**.
- **http://<ПУБЛИЧНЫЙ_IP>:8000/login** — страница входа (Яндекс или логин/пароль, в зависимости от настроек).

## 6. OAuth Яндекс (опционально)

Чтобы на странице входа была кнопка «Войти через Яндекс»:

1. [OAuth Яндекс](https://oauth.yandex.ru) → создать приложение, Callback URI: `http://<ВАШ_IP_ИЛИ_ДОМЕН>:8000/auth/yandex/callback`. Скопируйте ID и пароль в `secrets.env`: `YANDEX_OAUTH_CLIENT_ID`, `YANDEX_OAUTH_CLIENT_SECRET`.
2. В `secrets.env` задайте **точно** `BASE_URL=http://<ВАШ_IP>:8000` (без слэша в конце; тот же адрес, что в браузере). Иначе callback от Яндекса не совпадёт с redirect_uri и появятся «Ошибка входа через Yandex» или пропадут cookie.
3. Перезапустите web: `docker compose up -d --force-recreate web` (контейнер подхватит `env_file`: `.env` и `secrets.env`).
4. Если кнопка входа не видна — проверьте, что в `secrets.env` заданы `YANDEX_OAUTH_CLIENT_ID` и `YANDEX_OAUTH_CLIENT_SECRET`. После правки снова перезапустите web.

## Кратко: ссылка для входа

После деплоя на Yandex ВМ с открытым портом 8000:

- **Вход в систему:** `http://<ПУБЛИЧНЫЙ_IP_ВМ>:8000/login`
- **Главная (после входа):** `http://<ПУБЛИЧНЫЙ_IP_ВМ>:8000/`
- **Пользователи (контроль):** `http://<ПУБЛИЧНЫЙ_IP_ВМ>:8000/users`

Авторизация включена по умолчанию (`AUTH_OWN_ENABLED=1`): доступ к сервису только после идентификации. Без входа через Яндекс доступны только `/login`, `/auth/yandex`, `/auth/yandex/callback` и `/health`. Все остальные страницы и API требуют входа.

---

## Деплой последних изменений на уже работающую ВМ (Yandex)

Если у вас уже развёрнута ВМ с заполненной БД и вы обновили код (OAuth Яндекс, страница «Пользователи», защита API):

1. **Скопируйте обновлённый код на ВМ** (git pull в каталоге репо на ВМ или rsync/scp с локальной машины).

2. **На ВМ перейдите в каталог docker** (где лежат `docker-compose.yml`, `.env`, `secrets.env`):
   ```bash
   cd /path/to/tg_digest_system/tg_digest_system/docker
   ```

3. **Проверьте `secrets.env`** — должны быть заданы:
   - `AUTH_OWN_ENABLED=1`
   - `YANDEX_OAUTH_CLIENT_ID=...`, `YANDEX_OAUTH_CLIENT_SECRET=...` (из кабинета OAuth Яндекс)
   - `BASE_URL=http://<ПУБЛИЧНЫЙ_IP_ВМ>:8000` (без слэша в конце; тот же адрес, что в браузере)
   - `JWT_SECRET=<длинная случайная строка>`

4. **Если используете существующую БД** — примените миграцию 007 (таблицы `user_identities`, `audit_log`; в `users` разрешён NULL для `telegram_id`):
   ```bash
   # Вариант A: через контейнер migrate (подключение к вашей БД через PGHOST в .env)
   docker compose -f docker-compose.yml -f docker-compose.existing.yml run --rm migrate
   # Вариант B: вручную через psql к вашей БД
   psql -h <PGHOST> -U tg_digest -d tg_digest -f ../db/migrations/007_oauth_identities_audit.sql
   ```

5. **Пересоберите образ web и перезапустите сервисы:**
   ```bash
   ./deploy.sh --build
   ```
   Или только перезапуск web (если образ уже собран):
   ```bash
   docker compose up -d --force-recreate web
   ```

6. Откройте в браузере `http://<ПУБЛИЧНЫЙ_IP>:8000/login`, войдите через Яндекс; затем доступны главная, каналы, промпты, **Пользователи** (список пользователей и журнал аудита).
