#!/usr/bin/env python3
"""
config_json_loader.py — Расширенная загрузка JSON-конфигураций
Поддерживает:
- Автоматический поиск JSON файлов в папках config/prompts/ и config/channels/
- Загрузку промптов из prompts.json или отдельных файлов
- Новый формат channels.v2.json с prompts.digest
- Обратную совместимость со старым форматом
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)


class PromptLoader:
    """Загрузчик промптов из JSON"""
    
    def __init__(self, repo_dir: Path):
        self.repo_dir = Path(repo_dir)
        self.prompts: Dict[str, Dict[str, Any]] = {}
        self._load_prompts()
    
    def _load_prompts(self) -> None:
        """Загружает промпты из prompts.json и папки prompts/"""
        config_dir = self.repo_dir / "config"
        
        # 1. Загружаем общий файл prompts.json
        prompts_file = config_dir / "prompts.json"
        if prompts_file.exists():
            try:
                with open(prompts_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for prompt in data.get("prompts", []):
                        prompt_id = prompt.get("id")
                        if prompt_id:
                            self.prompts[prompt_id] = prompt
                    logger.info(f"Загружено {len(data.get('prompts', []))} промптов из prompts.json")
            except Exception as e:
                logger.warning(f"Не удалось загрузить prompts.json: {e}")
        
        # 2. Загружаем отдельные файлы из config/prompts/
        prompts_dir = config_dir / "prompts"
        if prompts_dir.exists() and prompts_dir.is_dir():
            for prompt_file in prompts_dir.glob("*.json"):
                try:
                    with open(prompt_file, "r", encoding="utf-8") as f:
                        prompt_data = json.load(f)
                        # Если это массив промптов
                        if isinstance(prompt_data, list):
                            for prompt in prompt_data:
                                prompt_id = prompt.get("id")
                                if prompt_id:
                                    self.prompts[prompt_id] = prompt
                        # Если это один промпт
                        elif isinstance(prompt_data, dict) and prompt_data.get("id"):
                            prompt_id = prompt_data["id"]
                            self.prompts[prompt_id] = prompt_data
                    logger.debug(f"Загружен промпт из {prompt_file.name}")
                except Exception as e:
                    logger.warning(f"Не удалось загрузить {prompt_file}: {e}")
        
        logger.info(f"Всего загружено промптов: {len(self.prompts)}")
    
    def get_prompt(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        """Получает промпт по ID"""
        return self.prompts.get(prompt_id)
    
    def get_system_prompt(self, prompt_id: str) -> Optional[str]:
        """Получает system_prompt из промпта"""
        prompt = self.get_prompt(prompt_id)
        if prompt:
            return prompt.get("system_prompt")
        return None
    
    def get_user_template(self, prompt_id: str) -> Optional[str]:
        """Получает user_template из промпта"""
        prompt = self.get_prompt(prompt_id)
        if prompt:
            return prompt.get("user_template")
        return None
    
    def format_user_template(self, prompt_id: str, **kwargs) -> Optional[str]:
        """Форматирует user_template с подстановкой переменных"""
        template = self.get_user_template(prompt_id)
        if not template:
            return None
        
        try:
            return template.format(**kwargs)
        except KeyError as e:
            logger.warning(f"Отсутствует переменная в шаблоне {prompt_id}: {e}")
            return template


class ChannelsLoader:
    """Загрузчик каналов из JSON"""
    
    def __init__(self, repo_dir: Path):
        self.repo_dir = Path(repo_dir)
        self.channels: List[Dict[str, Any]] = []
        self.recipient_groups: Dict[str, List[Dict[str, Any]]] = {}
        self._load_channels()
    
    def _load_channels(self) -> None:
        """Загружает каналы из channels.json/channels.v2.json и папки channels/"""
        config_dir = self.repo_dir / "config"
        
        # 1. Пробуем загрузить channels.v2.json (новый формат)
        channels_v2_file = config_dir / "channels.v2.json"
        if channels_v2_file.exists():
            try:
                with open(channels_v2_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.channels.extend(data.get("channels", []))
                    self.recipient_groups.update(data.get("recipient_groups", {}))
                    logger.info(f"Загружено {len(data.get('channels', []))} каналов из channels.v2.json")
            except Exception as e:
                logger.warning(f"Не удалось загрузить channels.v2.json: {e}")
        
        # 2. Пробуем загрузить channels.json (старый формат)
        channels_file = config_dir / "channels.json"
        if channels_file.exists() and not channels_v2_file.exists():
            try:
                with open(channels_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.channels.extend(data.get("channels", []))
                    logger.info(f"Загружено {len(data.get('channels', []))} каналов из channels.json")
            except Exception as e:
                logger.warning(f"Не удалось загрузить channels.json: {e}")
        
        # 3. Загружаем отдельные файлы из config/channels/
        channels_dir = config_dir / "channels"
        if channels_dir.exists() and channels_dir.is_dir():
            for channel_file in channels_dir.glob("*.json"):
                try:
                    with open(channel_file, "r", encoding="utf-8") as f:
                        channel_data = json.load(f)
                        # Если это массив каналов
                        if isinstance(channel_data, list):
                            self.channels.extend(channel_data)
                        # Если это один канал
                        elif isinstance(channel_data, dict):
                            if "channels" in channel_data:
                                self.channels.extend(channel_data["channels"])
                                self.recipient_groups.update(channel_data.get("recipient_groups", {}))
                            elif channel_data.get("id"):
                                self.channels.append(channel_data)
                    logger.debug(f"Загружен канал из {channel_file.name}")
                except Exception as e:
                    logger.warning(f"Не удалось загрузить {channel_file}: {e}")
        
        # 4. Разрешаем ссылки на группы получателей
        self._resolve_recipient_groups()
        
        logger.info(f"Всего загружено каналов: {len(self.channels)}")
    
    def _resolve_recipient_groups(self) -> None:
        """Заменяет recipients_group на recipients из групп"""
        for channel in self.channels:
            if "recipients_group" in channel and "recipients" not in channel:
                group_name = channel["recipients_group"]
                if group_name in self.recipient_groups:
                    channel["recipients"] = self.recipient_groups[group_name]
                    logger.debug(f"Разрешена группа получателей '{group_name}' для канала {channel.get('name')}")
                else:
                    logger.warning(f"Группа получателей '{group_name}' не найдена для канала {channel.get('name')}")
    
    def get_channels(self) -> List[Dict[str, Any]]:
        """Возвращает список всех каналов"""
        return self.channels
    
    def get_enabled_channels(self) -> List[Dict[str, Any]]:
        """Возвращает только включённые каналы"""
        return [ch for ch in self.channels if ch.get("enabled", True)]


def load_prompts_from_json(repo_dir: Path) -> PromptLoader:
    """Загружает промпты из JSON файлов"""
    return PromptLoader(repo_dir)


def load_channels_from_json(repo_dir: Path) -> ChannelsLoader:
    """Загружает каналы из JSON файлов"""
    return ChannelsLoader(repo_dir)


def get_prompt_text_from_json(
    prompt_loader: PromptLoader,
    prompt_id: Optional[str],
    fallback_path: Optional[Path] = None,
) -> Optional[str]:
    """
    Получает текст промпта из JSON или fallback на файл.
    
    Args:
        prompt_loader: Загрузчик промптов
        prompt_id: ID промпта из JSON (например, "digest_management")
        fallback_path: Путь к .md файлу (старый формат)
    
    Returns:
        Текст system_prompt или None
    """
    if prompt_id:
        system_prompt = prompt_loader.get_system_prompt(prompt_id)
        if system_prompt:
            return system_prompt
    
    # Fallback на старый формат (.md файл)
    if fallback_path and fallback_path.exists():
        try:
            return fallback_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Не удалось прочитать fallback промпт {fallback_path}: {e}")
    
    return None


if __name__ == "__main__":
    # Тест загрузки
    import sys
    logging.basicConfig(level=logging.INFO)
    
    repo_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/app")
    
    print("=== Тест загрузки промптов ===")
    prompt_loader = load_prompts_from_json(repo_dir)
    print(f"Загружено промптов: {len(prompt_loader.prompts)}")
    for prompt_id in prompt_loader.prompts:
        print(f"  - {prompt_id}: {prompt_loader.prompts[prompt_id].get('name')}")
    
    print("\n=== Тест загрузки каналов ===")
    channels_loader = load_channels_from_json(repo_dir)
    print(f"Загружено каналов: {len(channels_loader.channels)}")
    for channel in channels_loader.get_enabled_channels():
        print(f"  - {channel.get('name')} (ID: {channel.get('id')})")
        prompts = channel.get("prompts", {})
        if prompts:
            print(f"    Промпты: digest={prompts.get('digest')}, consolidated_doc={prompts.get('consolidated_doc')}")
