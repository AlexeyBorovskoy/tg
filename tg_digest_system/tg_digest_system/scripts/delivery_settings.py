#!/usr/bin/env python3
"""
delivery_settings.py — Настройки отправки дайджестов по каждому подконтрольному чату.
Читает config/digest_delivery.json: важные чаты (полный дайджест), ознакомительные (кратко/без файла).
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ChannelDeliverySettings:
    """Настройки доставки дайджеста по одному чату (каналу)."""
    # important = полный дайджест (текст + файл), informational = ознакомительный (кратко или только текст)
    importance: str  # "important" | "informational"
    send_file: bool = True
    send_text: bool = True
    # Для ознакомительных: ограничить длину текста в сообщении (None = без ограничения)
    text_max_chars: Optional[int] = None
    # Краткое резюме вместо полного текста (если True, в сообщение идёт только заголовок + первые N символов)
    summary_only: bool = False


DEFAULT_CHANNEL_SETTINGS = ChannelDeliverySettings(
    importance="important",
    send_file=True,
    send_text=True,
    text_max_chars=None,
    summary_only=False,
)

# Значения по умолчанию для ознакомительных чатов
DEFAULT_INFORMATIONAL = ChannelDeliverySettings(
    importance="informational",
    send_file=False,
    send_text=True,
    text_max_chars=500,
    summary_only=True,
)


def _config_dir() -> Path:
    """Каталог с конфигами (рядом с channels.json)."""
    config_file = os.environ.get("CONFIG_FILE", "/app/config/channels.json")
    return Path(config_file).resolve().parent


def _delivery_file_path() -> Path:
    """Путь к файлу настроек доставки."""
    env_path = os.environ.get("DIGEST_DELIVERY_FILE", "")
    if env_path:
        return Path(env_path).resolve()
    return _config_dir() / "digest_delivery.json"


def load_delivery_settings() -> dict[int, ChannelDeliverySettings]:
    """
    Загружает настройки доставки из config/digest_delivery.json.
    Ключ — telegram chat id (int). Если файла нет — возвращает пустой dict (используются дефолты).
    """
    path = _delivery_file_path()
    if not path.exists():
        logger.debug("Файл настроек доставки не найден: %s, используются дефолты по каналу", path)
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("Не удалось прочитать %s: %s", path, e)
        return {}

    defaults = data.get("defaults", {})
    default_importance = defaults.get("importance", "important")
    default_send_file = defaults.get("send_file", True)
    default_send_text = defaults.get("send_text", True)
    default_text_max_chars = defaults.get("text_max_chars")
    default_summary_only = defaults.get("summary_only", False)

    result: dict[int, ChannelDeliverySettings] = {}
    for channel_key, raw in data.get("channels", {}).items():
        try:
            channel_id = int(channel_key)
        except (ValueError, TypeError):
            logger.warning("Некорректный ID канала в digest_delivery.json: %s", channel_key)
            continue
        importance = raw.get("importance", default_importance)
        send_file = raw.get("send_file", default_send_file)
        send_text = raw.get("send_text", default_send_text)
        text_max_chars = raw.get("text_max_chars", default_text_max_chars)
        summary_only = raw.get("summary_only", default_summary_only)
        # Для informational можно подставить разумные значения по умолчанию
        if importance == "informational" and "send_file" not in raw:
            send_file = raw.get("send_file", DEFAULT_INFORMATIONAL.send_file)
        if importance == "informational" and "text_max_chars" not in raw and default_text_max_chars is None:
            text_max_chars = DEFAULT_INFORMATIONAL.text_max_chars
        result[channel_id] = ChannelDeliverySettings(
            importance=importance,
            send_file=send_file,
            send_text=send_text,
            text_max_chars=text_max_chars,
            summary_only=summary_only,
        )
    logger.info("Загружены настройки доставки для %s чатов из %s", len(result), path)
    return result


def get_delivery_settings_for_channel(
    channel_id: int,
    settings_cache: Optional[dict[int, ChannelDeliverySettings]] = None,
) -> ChannelDeliverySettings:
    """Возвращает настройки доставки для канала. При отсутствии — дефолт (important, полная доставка)."""
    if settings_cache is None:
        settings_cache = load_delivery_settings()
    return settings_cache.get(channel_id, DEFAULT_CHANNEL_SETTINGS)
