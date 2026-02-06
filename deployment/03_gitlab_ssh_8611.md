# Deployment: доступ к GitLab по SSH (порт 8611)

## 1. Исходные условия
- GitLab доступен по нестандартному SSH порту: 8611.
- Для автоматизации используется отдельный ключ ED25519.

## 2. Рекомендуемая конфигурация ~/.ssh/config
Пример:

Host gitlab-ripas-8611
HostName gitlab.ripas.ru
Port 8611
User git
IdentityFile /home/ripas/.ssh/gitlab_tg_rag_reports
IdentitiesOnly yes
StrictHostKeyChecking accept-new
ServerAliveInterval 15
ServerAliveCountMax 4

## 3. Проверка
