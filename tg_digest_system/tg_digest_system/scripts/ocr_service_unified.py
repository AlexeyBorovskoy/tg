#!/usr/bin/env python3
"""
ocr_service_unified.py — Универсальный OCR-сервис с поддержкой облачных и локальных провайдеров
"""

import os
import os
import hashlib
import time
import logging
from typing import Optional, Tuple, Dict, Any

from config import Config
from database import Database

logger = logging.getLogger(__name__)


class UnifiedOCRService:
    """Универсальный OCR-сервис с поддержкой облачных и локальных провайдеров"""
    
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        
        # Определяем провайдера из конфига или env
        self.provider = os.environ.get("OCR_PROVIDER", "tesseract").lower()
        if hasattr(config.defaults, 'ocr_provider'):
            self.provider = config.defaults.ocr_provider.lower()
        
        self.fallback_enabled = os.environ.get("OCR_FALLBACK_ENABLED", "true").lower() == "true"
        
        # Инициализация провайдеров
        self.primary = None
        self.fallback = None
        
        self._init_providers()
    
    def _init_providers(self) -> None:
        """Инициализирует провайдеры OCR"""
        
        # Основной провайдер (облачный)
        if self.provider == "google_vision":
            try:
                from ocr_cloud import GoogleVisionOCR
                self.primary = GoogleVisionOCR()
                logger.info("Основной OCR провайдер: Google Vision")
            except Exception as e:
                logger.warning(f"Не удалось инициализировать Google Vision: {e}")
                self.primary = None
        
        elif self.provider == "yandex_vision":
            try:
                from ocr_cloud import YandexVisionOCR
                self.primary = YandexVisionOCR()
                logger.info("Основной OCR провайдер: Yandex Vision")
            except Exception as e:
                logger.warning(f"Не удалось инициализировать Yandex Vision: {e}")
                self.primary = None
        
        elif self.provider == "ocr_space":
            try:
                from ocr_cloud import OCRSpaceOCR
                self.primary = OCRSpaceOCR()
                logger.info("Основной OCR провайдер: OCR.space (бесплатный, без карты)")
            except Exception as e:
                logger.warning(f"Не удалось инициализировать OCR.space: {e}")
                self.primary = None
        
        elif self.provider == "easyocr":
            try:
                from ocr_cloud import EasyOCROCR
                self.primary = EasyOCROCR()
                logger.info("Основной OCR провайдер: EasyOCR (бесплатный, без регистрации)")
            except Exception as e:
                logger.warning(f"Не удалось инициализировать EasyOCR: {e}")
                self.primary = None
        
        # Fallback на локальный Tesseract
        if self.fallback_enabled:
            try:
                from ocr import OCRService as TesseractOCR
                self.fallback = TesseractOCR(self.config, self.db)
                logger.info("Fallback OCR провайдер: Tesseract")
            except Exception as e:
                logger.warning(f"Не удалось инициализировать Tesseract fallback: {e}")
                self.fallback = None
        
        if not self.primary and not self.fallback:
            logger.error("Нет доступных OCR провайдеров!")
    
    async def process_image(self, image_bytes: bytes) -> Tuple[str, Dict[str, Any]]:
        """
        Обрабатывает изображение через облачный сервис или fallback.
        
        Args:
            image_bytes: Байты изображения
        
        Returns:
            Tuple[text, metadata] где metadata содержит:
            - provider: использованный провайдер
            - duration_sec: время обработки
            - error: ошибка если была
            - image_hash: хэш изображения для кэширования
        """
        t0 = time.monotonic()
        metadata: Dict[str, Any] = {}
        
        # Вычисляем хэш для кэширования
        image_hash = hashlib.sha256(image_bytes).hexdigest()
        metadata["image_hash"] = image_hash
        
        # Проверяем кэш в БД (через sha256 в tg.media)
        try:
            cached_text = self.db.get_ocr_by_image_hash(image_hash)
            if cached_text:
                logger.debug(f"OCR cache hit: {image_hash[:8]}...")
                metadata["provider"] = "cache"
                metadata["duration_sec"] = time.monotonic() - t0
                return cached_text, metadata
        except Exception as e:
            logger.debug(f"Кэш недоступен (это нормально при первом запуске): {e}")
        
        # Пробуем облачный сервис
        if self.primary:
            try:
                if self.provider == "google_vision":
                    text = await self.primary.recognize(image_bytes, languages=["ru", "en"])
                elif self.provider == "ocr_space":
                    text = await self.primary.recognize(image_bytes, language="rus")
                else:  # yandex_vision, easyocr
                    text = await self.primary.recognize(image_bytes)
                
                metadata["provider"] = self.provider
                metadata["duration_sec"] = time.monotonic() - t0
                logger.info(f"OCR via {self.provider}: {len(text)} chars, {metadata['duration_sec']:.2f}s")
                
                # Сохраняем в кэш
                if text:
                    self._save_to_cache(image_hash, text)
                
                return text, metadata
                
            except Exception as e:
                logger.warning(f"Cloud OCR failed ({self.provider}): {e}, using fallback")
                metadata["cloud_error"] = str(e)
        
        # Fallback на Tesseract
        if self.fallback:
            try:
                text, confidence = self.fallback.process_image(image_bytes)
                metadata["provider"] = "tesseract"
                metadata["duration_sec"] = time.monotonic() - t0
                metadata["confidence"] = confidence
                logger.info(f"OCR via fallback (Tesseract): {len(text)} chars, {metadata['duration_sec']:.2f}s")
                
                # Сохраняем в кэш
                if text:
                    self._save_to_cache(image_hash, text)
                
                return text, metadata
                
            except Exception as e:
                logger.error(f"Fallback OCR failed: {e}")
                metadata["error"] = str(e)
        
        metadata["duration_sec"] = time.monotonic() - t0
        return "", metadata
    
    def _save_to_cache(self, image_hash: str, text: str) -> None:
        """
        Сохраняет результат OCR в кэш БД.
        Примечание: кэш работает через sha256 в tg.media, 
        поэтому при сохранении OCR через save_ocr_text кэш обновляется автоматически.
        """
        logger.debug(f"OCR result ready for cache: hash={image_hash[:8]}..., len={len(text)}")
    
    async def process_pending_media_async(self, limit: int = 10, user_id: Optional[int] = None) -> int:
        """
        Асинхронная обработка медиафайлов без OCR.
        
        Args:
            limit: Максимум файлов за раз
            user_id: ID пользователя для фильтрации медиа
        
        Returns:
            Количество обработанных файлов
        """
        import asyncio
        from pathlib import Path
        
        media_list = self.db.get_media_without_ocr(limit=limit, user_id=user_id)
        processed = 0
        
        for media in media_list:
            try:
                # Получаем данные изображения
                if media.get("file_data"):
                    image_data = bytes(media["file_data"])
                elif media.get("local_path"):
                    path = Path(media["local_path"])
                    if not path.exists():
                        logger.warning(f"Файл не найден: {path}")
                        continue
                    image_data = path.read_bytes()
                else:
                    continue
                
                # OCR (асинхронный)
                text, metadata = await self.process_image(image_data)
                
                if text:
                    media_user_id = media.get("user_id") or user_id
                    self.db.save_ocr_text(
                        media_id=media["id"],
                        peer_type=media["peer_type"],
                        peer_id=media["peer_id"],
                        msg_id=media["msg_id"],
                        ocr_text=text,
                        ocr_model=f"{metadata.get('provider', 'unknown')}",
                        ocr_confidence=metadata.get("confidence"),
                        user_id=media_user_id,
                    )
                    processed += 1
                    logger.info(
                        f"OCR: msg_id={media['msg_id']}, provider={metadata.get('provider')}, "
                        f"{len(text)} chars, {metadata.get('duration_sec', 0):.2f}s"
                    )
                    
            except Exception as e:
                logger.error(f"Ошибка обработки media_id={media.get('id')}: {e}", exc_info=True)
        
        return processed
