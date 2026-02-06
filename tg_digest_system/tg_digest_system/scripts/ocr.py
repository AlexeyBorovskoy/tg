#!/usr/bin/env python3
"""
ocr.py — OCR обработка изображений через Tesseract
"""

import io
import logging
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

from config import Config
from database import Database

logger = logging.getLogger(__name__)


class OCRService:
    """Сервис OCR через Tesseract"""
    
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.languages = "+".join(config.defaults.ocr_languages)
    
    def process_image(self, image_data: bytes) -> Tuple[str, Optional[float]]:
        """
        Выполняет OCR на изображении.
        
        Args:
            image_data: Байты изображения
        
        Returns:
            Tuple[text, confidence]: Распознанный текст и уверенность (0-1)
        """
        try:
            # Проверяем, что это валидное изображение
            img = Image.open(io.BytesIO(image_data))
            
            # Конвертируем в RGB если нужно
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            
            # Сохраняем во временный буфер
            img_buffer = io.BytesIO()
            img.save(img_buffer, format="PNG")
            img_buffer.seek(0)
            
            # Вызываем Tesseract
            cmd = [
                "tesseract",
                "stdin",
                "stdout",
                "-l", self.languages,
                "--psm", "6",  # Assume uniform block of text
            ]
            
            result = subprocess.run(
                cmd,
                input=img_buffer.read(),
                capture_output=True,
                timeout=30,
            )
            
            if result.returncode != 0:
                logger.warning(f"Tesseract вернул код {result.returncode}: {result.stderr.decode()}")
                return "", None
            
            text = result.stdout.decode("utf-8", errors="replace")
            text = self._normalize_text(text)
            
            # Tesseract не возвращает confidence в stdout режиме
            # Можно добавить --oem 1 и парсить HOCR для confidence
            confidence = None
            
            logger.debug(f"OCR: {len(text)} символов")
            return text, confidence
            
        except subprocess.TimeoutExpired:
            logger.error("Tesseract timeout")
            return "", None
        except Exception as e:
            logger.error(f"Ошибка OCR: {e}")
            return "", None
    
    def _normalize_text(self, text: str) -> str:
        """Нормализует OCR-текст"""
        # Убираем множественные пробелы и переносы
        import re
        text = text.strip()
        text = re.sub(r"\r\n", "\n", text)
        text = re.sub(r"\r", "\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        
        # Ограничиваем длину
        if len(text) > 8000:
            text = text[:8000] + "\n[...TRUNCATED...]"
        
        return text
    
    def process_pending_media(self, limit: int = 10) -> int:
        """
        Обрабатывает медиафайлы без OCR.
        
        Args:
            limit: Максимум файлов за раз
        
        Returns:
            Количество обработанных файлов
        """
        media_list = self.db.get_media_without_ocr(limit=limit)
        processed = 0
        
        for media in media_list:
            try:
                # Получаем данные изображения
                if media["file_data"]:
                    image_data = bytes(media["file_data"])
                elif media["local_path"]:
                    path = Path(media["local_path"])
                    if not path.exists():
                        logger.warning(f"Файл не найден: {path}")
                        continue
                    image_data = path.read_bytes()
                else:
                    continue
                
                # OCR
                text, confidence = self.process_image(image_data)
                
                if text:
                    self.db.save_ocr_text(
                        media_id=media["id"],
                        peer_type=media["peer_type"],
                        peer_id=media["peer_id"],
                        msg_id=media["msg_id"],
                        ocr_text=text,
                        ocr_model=f"tesseract-5 {self.languages}",
                        ocr_confidence=confidence,
                    )
                    processed += 1
                    logger.info(f"OCR: msg_id={media['msg_id']}, {len(text)} символов")
                    
            except Exception as e:
                logger.error(f"Ошибка обработки media_id={media['id']}: {e}")
        
        return processed


def check_tesseract() -> bool:
    """Проверяет доступность Tesseract"""
    try:
        result = subprocess.run(
            ["tesseract", "--version"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            version = result.stdout.decode().split("\n")[0]
            logger.info(f"Tesseract доступен: {version}")
            return True
        return False
    except Exception as e:
        logger.error(f"Tesseract недоступен: {e}")
        return False
