#!/usr/bin/env python3
"""
config.py — Загрузка и валидация конфигурации
"""

import os
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Recipient:
    """Получатель дайджеста"""
    telegram_id: int
    name: str
    role: str = ""
    send_file: bool = True
    send_text: bool = True


@dataclass
class Channel:
    """Конфигурация канала"""
    id: int
    name: str
    prompt_file: str
    recipients: list[Recipient]
    description: str = ""
    enabled: bool = True
    peer_type: str = "channel"
    poll_interval_minutes: int = 30
    # Сводный инженерный документ (docs-as-code): путь к файлу и промпт
    consolidated_doc_path: str = ""
    consolidated_doc_prompt_file: str = "prompts/consolidated_engineering.md"


@dataclass
class Defaults:
    """Настройки по умолчанию"""
    poll_interval_minutes: int = 30
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_max_tokens: int = 2000
    llm_temperature: float = 0.1
    ocr_enabled: bool = True
    ocr_languages: list[str] = field(default_factory=lambda: ["rus", "eng"])
    media_save_to_db: bool = True
    digest_format: str = "markdown"
    timezone: str = "Europe/Moscow"


@dataclass
class Config:
    """Полная конфигурация системы"""
    channels: list[Channel]
    defaults: Defaults
    
    # Из переменных окружения
    tg_api_id: int = 0
    tg_api_hash: str = ""
    tg_bot_token: str = ""
    tg_session_file: str = ""
    # Чат для уведомлений о шагах пайплайна (отладка/мониторинг)
    tg_step_notify_chat_id: Optional[int] = None
    
    openai_api_key: str = ""
    openai_base_url: str = ""  # для Artemox и др. OpenAI-совместимых API
    openai_model: str = "gpt-4o"
    openai_max_tokens: int = 2000
    openai_temperature: float = 0.1
    
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_database: str = "tg_digest"
    pg_user: str = ""
    pg_password: str = ""
    
    repo_dir: Path = field(default_factory=lambda: Path("/app"))
    prompts_dir: Path = field(default_factory=lambda: Path("/app/prompts"))
    media_dir: Path = field(default_factory=lambda: Path("/app/media"))
    logs_dir: Path = field(default_factory=lambda: Path("/app/logs"))
    
    # GitLab (gitlab.ripas.ru): пуш дайджестов и сводных документов
    gitlab_enabled: bool = False
    gitlab_repo_url: str = ""
    gitlab_branch: str = "main"
    gitlab_ssh_key: str = ""
    
    debug: bool = False


def load_config(config_path: Optional[str] = None) -> Config:
    """
    Загружает конфигурацию из JSON-файла и переменных окружения.
    
    Args:
        config_path: Путь к channels.json. Если None, берётся из CONFIG_FILE env.
    
    Returns:
        Config: Объект конфигурации
    """
    # Путь к конфигу
    if config_path is None:
        config_path = os.environ.get("CONFIG_FILE", "/app/config/channels.json")
    
    config_file = Path(config_path)
    
    if not config_file.exists():
        raise FileNotFoundError(f"Файл конфигурации не найден: {config_file}")
    
    # Загружаем JSON
    with open(config_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Парсим каналы
    channels = []
    for ch in data.get("channels", []):
        recipients = [
            Recipient(
                telegram_id=r["telegram_id"],
                name=r["name"],
                role=r.get("role", ""),
                send_file=r.get("send_file", True),
                send_text=r.get("send_text", True),
            )
            for r in ch.get("recipients", [])
        ]
        
        channel = Channel(
            id=ch["id"],
            name=ch["name"],
            description=ch.get("description", ""),
            enabled=ch.get("enabled", True),
            peer_type=ch.get("peer_type", "channel"),
            prompt_file=ch["prompt_file"],
            poll_interval_minutes=ch.get("poll_interval_minutes", 30),
            recipients=recipients,
            consolidated_doc_path=ch.get("consolidated_doc_path", ""),
            consolidated_doc_prompt_file=ch.get("consolidated_doc_prompt_file", "prompts/consolidated_engineering.md"),
        )
        channels.append(channel)
    
    # Парсим defaults
    d = data.get("defaults", {})
    defaults = Defaults(
        poll_interval_minutes=d.get("poll_interval_minutes", 30),
        llm_provider=d.get("llm_provider", "openai"),
        llm_model=d.get("llm_model", "gpt-4o"),
        llm_max_tokens=d.get("llm_max_tokens", 2000),
        llm_temperature=d.get("llm_temperature", 0.1),
        ocr_enabled=d.get("ocr_enabled", True),
        ocr_languages=d.get("ocr_languages", ["rus", "eng"]),
        media_save_to_db=d.get("media_save_to_db", True),
        digest_format=d.get("digest_format", "markdown"),
        timezone=d.get("timezone", "Europe/Moscow"),
    )
    
    # Собираем Config из env
    config = Config(
        channels=channels,
        defaults=defaults,
        
        # Telegram
        tg_api_id=int(os.environ.get("TG_API_ID", "0")),
        tg_api_hash=os.environ.get("TG_API_HASH", ""),
        tg_bot_token=os.environ.get("TG_BOT_TOKEN", ""),
        tg_session_file=os.environ.get("TG_SESSION_FILE", "/app/data/telethon.session"),
        tg_step_notify_chat_id=int(os.environ.get("TG_STEP_NOTIFY_CHAT_ID", "0")) or None,
        
        # OpenAI (или совместимый API: Artemox и т.д.)
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        openai_base_url=os.environ.get("OPENAI_BASE_URL", ""),
        openai_model=os.environ.get("OPENAI_MODEL", defaults.llm_model),
        openai_max_tokens=int(os.environ.get("OPENAI_MAX_TOKENS", str(defaults.llm_max_tokens))),
        openai_temperature=float(os.environ.get("OPENAI_TEMPERATURE", str(defaults.llm_temperature))),
        
        # PostgreSQL
        pg_host=os.environ.get("PGHOST", "localhost"),
        pg_port=int(os.environ.get("PGPORT", "5432")),
        pg_database=os.environ.get("PGDATABASE", "tg_digest"),
        pg_user=os.environ.get("PGUSER", ""),
        pg_password=os.environ.get("PGPASSWORD", ""),
        
        # Пути
        repo_dir=Path(os.environ.get("REPO_DIR", "/app")),
        prompts_dir=Path(os.environ.get("PROMPTS_DIR", "/app/prompts")),
        media_dir=Path(os.environ.get("MEDIA_DIR", "/app/media")),
        logs_dir=Path(os.environ.get("LOGS_DIR", "/app/logs")),
        
        gitlab_enabled=os.environ.get("GITLAB_ENABLED", "0") == "1",
        gitlab_repo_url=os.environ.get("GITLAB_REPO_URL", ""),
        gitlab_branch=os.environ.get("GITLAB_BRANCH", "main"),
        gitlab_ssh_key=os.environ.get("GITLAB_SSH_KEY", ""),
        
        debug=os.environ.get("DEBUG", "0") == "1",
    )
    
    # Валидация
    _validate_config(config)
    
    logger.info(f"Загружена конфигурация: {len(config.channels)} каналов")
    return config


def _validate_config(config: Config) -> None:
    """Проверяет корректность конфигурации"""
    errors = []
    
    if not config.tg_api_id:
        errors.append("TG_API_ID не установлен")
    
    if not config.tg_api_hash:
        errors.append("TG_API_HASH не установлен")
    
    if not config.tg_bot_token:
        errors.append("TG_BOT_TOKEN не установлен")
    
    if not config.openai_api_key:
        errors.append("OPENAI_API_KEY не установлен")
    
    if not config.pg_user or not config.pg_password:
        errors.append("PostgreSQL credentials не установлены")
    
    # Проверяем файлы промптов (используем prompts_dir вместо repo_dir)
    for ch in config.channels:
        prompt_path = config.prompts_dir / Path(ch.prompt_file).name
        if not prompt_path.exists():
            errors.append(f"Файл промпта не найден: {prompt_path}")
        if ch.consolidated_doc_path:
            cons_prompt_path = config.prompts_dir / Path(ch.consolidated_doc_prompt_file).name
            if not cons_prompt_path.exists():
                errors.append(f"Файл промпта сводного документа не найден: {cons_prompt_path}")
    
    if errors:
        for err in errors:
            logger.error(f"Ошибка конфигурации: {err}")
        raise ValueError(f"Ошибки конфигурации: {', '.join(errors)}")


def get_enabled_channels(config: Config) -> list[Channel]:
    """Возвращает только включённые каналы"""
    return [ch for ch in config.channels if ch.enabled]


def get_prompt(config: Config, channel: Channel) -> str:
    """Загружает текст промпта для канала (дайджест)
    
    Приоритет загрузки:
    1. Из таблицы channel_prompts (БД) - промпт с is_default=true
    2. Из поля prompt_text таблицы web_channels (обратная совместимость)
    3. Из файла (fallback)
    """
    # Пробуем загрузить из БД
    user_id = getattr(channel, 'user_id', None)
    
    try:
        from config_db import get_prompt_from_db, get_prompt_from_web_channels
        
        # Сначала из channel_prompts
        prompt_text = get_prompt_from_db(config, channel.id, 'digest', user_id)
        if prompt_text:
            logger.debug(f"Промпт для дайджестов загружен из БД (channel_prompts) для канала {channel.id}")
            return prompt_text
        
        # Затем из web_channels (обратная совместимость)
        prompt_text = get_prompt_from_web_channels(config, channel.id, 'digest', user_id)
        if prompt_text:
            logger.debug(f"Промпт для дайджестов загружен из БД (web_channels) для канала {channel.id}")
            return prompt_text
    except Exception as e:
        logger.warning(f"Не удалось загрузить промпт из БД для канала {channel.id}: {e}, используем файл")
    
    # Fallback: загружаем из файла
    prompt_path = config.prompts_dir / Path(channel.prompt_file).name
    
    if not prompt_path.exists():
        raise FileNotFoundError(f"Промпт не найден ни в БД, ни в файле: {prompt_path}")
    
    logger.debug(f"Промпт для дайджестов загружен из файла {prompt_path} для канала {channel.id}")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


def get_consolidated_prompt(config: Config, channel: Channel) -> str:
    """Загружает текст промпта для сводного инженерного документа
    
    Приоритет загрузки:
    1. Из таблицы channel_prompts (БД) - промпт с is_default=true
    2. Из поля consolidated_doc_prompt_text таблицы web_channels (обратная совместимость)
    3. Из файла (fallback)
    """
    # Пробуем загрузить из БД
    user_id = getattr(channel, 'user_id', None)
    
    try:
        from config_db import get_prompt_from_db, get_prompt_from_web_channels
        
        # Сначала из channel_prompts
        prompt_text = get_prompt_from_db(config, channel.id, 'consolidated', user_id)
        if prompt_text:
            logger.debug(f"Промпт для сводного документа загружен из БД (channel_prompts) для канала {channel.id}")
            return prompt_text
        
        # Затем из web_channels (обратная совместимость)
        prompt_text = get_prompt_from_web_channels(config, channel.id, 'consolidated', user_id)
        if prompt_text:
            logger.debug(f"Промпт для сводного документа загружен из БД (web_channels) для канала {channel.id}")
            return prompt_text
    except Exception as e:
        logger.warning(f"Не удалось загрузить промпт сводного документа из БД для канала {channel.id}: {e}, используем файл")
    
    # Fallback: загружаем из файла
    prompt_path = config.prompts_dir / Path(channel.consolidated_doc_prompt_file).name
    
    if not prompt_path.exists():
        raise FileNotFoundError(f"Промпт сводного документа не найден ни в БД, ни в файле: {prompt_path}")
    
    logger.debug(f"Промпт для сводного документа загружен из файла {prompt_path} для канала {channel.id}")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    # Тест загрузки
    logging.basicConfig(level=logging.DEBUG)
    
    try:
        cfg = load_config("config/channels.json")
        print(f"Загружено каналов: {len(cfg.channels)}")
        for ch in cfg.channels:
            print(f"  - {ch.name} (ID: {ch.id}, enabled: {ch.enabled})")
            print(f"    Получатели: {len(ch.recipients)}")
    except Exception as e:
        print(f"Ошибка: {e}")
