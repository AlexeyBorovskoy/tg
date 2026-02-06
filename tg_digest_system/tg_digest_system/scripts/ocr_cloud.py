#!/usr/bin/env python3
"""
ocr_cloud.py — Облачные OCR провайдеры
- Google Vision (требует карту)
- Yandex Vision (требует карту)
- OCR.space (бесплатно, без карты, 25,000 запросов/мес) ⭐ РЕКОМЕНДУЕТСЯ
- EasyOCR (бесплатно, без регистрации)
"""

import os
import base64
import logging
from typing import Optional

try:
    import aiohttp
except ImportError:
    aiohttp = None

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger(__name__)


class GoogleVisionOCR:
    """OCR через Google Cloud Vision API"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GOOGLE_VISION_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_VISION_API_KEY не установлен")
        
        try:
            from google.cloud import vision
            self.client = vision.ImageAnnotatorClient(
                client_options={"api_key": self.api_key}
            )
            logger.info("Google Vision OCR инициализирован")
        except ImportError:
            raise ImportError("Установите google-cloud-vision: pip install google-cloud-vision")
        except Exception as e:
            logger.error(f"Ошибка инициализации Google Vision: {e}")
            raise
    
    async def recognize(self, image_bytes: bytes, languages: list[str] = ["ru", "en"]) -> str:
        """
        Распознаёт текст на изображении через Google Vision API.
        
        Args:
            image_bytes: Байты изображения
            languages: Список языков для распознавания
        
        Returns:
            Распознанный текст
        """
        try:
            from google.cloud import vision
            
            image = vision.Image(content=image_bytes)
            
            # Настройки для улучшения качества
            image_context = vision.ImageContext(
                language_hints=languages
            )
            
            response = self.client.text_detection(
                image=image,
                image_context=image_context
            )
            
            if response.error.message:
                raise Exception(f"Google Vision API error: {response.error.message}")
            
            texts = response.text_annotations
            if texts:
                # Первый результат — весь текст
                return texts[0].description
            
            return ""
            
        except Exception as e:
            logger.error(f"Google Vision OCR error: {e}")
            raise


class YandexVisionOCR:
    """OCR через Yandex Vision API"""
    
    def __init__(self, api_key: Optional[str] = None, folder_id: Optional[str] = None):
        self.api_key = api_key or os.environ.get("YANDEX_VISION_API_KEY")
        self.folder_id = folder_id or os.environ.get("YANDEX_FOLDER_ID")
        
        if not self.api_key:
            raise ValueError("YANDEX_VISION_API_KEY не установлен")
        if not self.folder_id:
            raise ValueError("YANDEX_FOLDER_ID не установлен")
        
        self.base_url = "https://vision.api.cloud.yandex.net/vision/v1"
        logger.info("Yandex Vision OCR инициализирован")
    
    async def recognize(self, image_bytes: bytes) -> str:
        """
        Распознаёт текст на изображении через Yandex Vision API.
        
        Args:
            image_bytes: Байты изображения
        
        Returns:
            Распознанный текст
        """
        if aiohttp is None:
            raise ImportError("Установите aiohttp: pip install aiohttp")
        
        try:
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/textDetection",
                    headers={
                        "Authorization": f"Api-Key {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "folderId": self.folder_id,
                        "analyzeSpecs": [{
                            "content": image_b64,
                            "features": [{"type": "TEXT_DETECTION"}]
                        }]
                    },
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        raise Exception(f"Yandex Vision API error {resp.status}: {error_text}")
                    
                    data = await resp.json()
                    
                    if "results" in data and data["results"]:
                        text_annotations = data["results"][0].get("textDetection", {}).get("pages", [])
                        if text_annotations:
                            # Собираем текст из всех блоков
                            lines = []
                            for page in text_annotations:
                                for block in page.get("blocks", []):
                                    for line in block.get("lines", []):
                                        line_text = line.get("text", "")
                                        if line_text:
                                            lines.append(line_text)
                            return "\n".join(lines)
                    
                    return ""
                    
        except Exception as e:
            logger.error(f"Yandex Vision OCR error: {e}")
            raise


class OCRSpaceOCR:
    """OCR через OCR.space API (бесплатно, без карты, 25,000 запросов/мес)"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("OCR_SPACE_API_KEY")
        if not self.api_key:
            raise ValueError("OCR_SPACE_API_KEY не установлен. Получите бесплатный ключ на https://ocr.space/ocrapi/freekey")
        
        self.base_url = "https://api.ocr.space/parse/image"
        logger.info("OCR.space OCR инициализирован (бесплатный tier)")
    
    async def recognize(self, image_bytes: bytes, language: str = "rus") -> str:
        """
        Распознаёт текст на изображении через OCR.space API.
        
        Args:
            image_bytes: Байты изображения (макс 1 MB для free tier)
            language: Язык распознавания ("rus", "eng", "rus+eng")
        
        Returns:
            Распознанный текст
        """
        # Используем aiohttp если доступен, иначе requests
        use_aiohttp = aiohttp is not None
        
        if not use_aiohttp and requests is None:
            raise ImportError("Установите aiohttp или requests: pip install aiohttp или pip install requests")
        
        # Проверяем размер файла (лимит 1 MB для free tier)
        if len(image_bytes) > 1024 * 1024:
            raise ValueError(f"Размер файла {len(image_bytes)} bytes превышает лимит 1 MB для free tier")
        
        try:
            # Кодируем в base64
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            
            # Определяем MIME type по первым байтам
            mime_type = "image/jpeg"
            if image_bytes[:4] == b'\x89PNG':
                mime_type = "image/png"
            elif image_bytes[:2] == b'\xff\xd8':
                mime_type = "image/jpeg"
            
            payload = {
                "apikey": self.api_key,
                "base64Image": f"data:{mime_type};base64,{image_b64}",
                "language": language,
                "isOverlayRequired": False,
                "OCREngine": 2,  # Engine 2 - лучший для русского
                "detectOrientation": True,
                "scale": True,
                "isCreateSearchablePdf": False,
                "isSearchablePdfHideTextLayer": False
            }
            
            if use_aiohttp:
                # Асинхронный вариант через aiohttp
                # Настройка прокси из переменных окружения
                proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
                connector = None
                if proxy:
                    try:
                        # Для SOCKS5 нужен aiohttp-socks
                        if proxy.startswith("socks5://"):
                            try:
                                from aiohttp_socks import ProxyConnector
                                connector = ProxyConnector.from_url(proxy)
                                logger.debug(f"OCR.space использует SOCKS5 прокси: {proxy}")
                            except ImportError:
                                logger.warning("aiohttp-socks не установлен, SOCKS5 прокси не будет работать. Установите: pip install aiohttp-socks")
                        else:
                            # HTTP прокси работает напрямую
                            connector = aiohttp.ProxyConnector.from_url(proxy)
                            logger.debug(f"OCR.space использует HTTP прокси: {proxy}")
                    except Exception as e:
                        logger.warning(f"Ошибка настройки прокси для OCR.space: {e}")
                
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.post(
                        self.base_url,
                        data=payload,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as resp:
                        if resp.status != 200:
                            error_text = await resp.text()
                            raise Exception(f"OCR.space API error {resp.status}: {error_text}")
                        
                        result = await resp.json()
            else:
                # Синхронный вариант через requests (в async функции используем asyncio.to_thread)
                import asyncio
                def sync_request():
                    # requests автоматически использует HTTP_PROXY/HTTPS_PROXY из окружения
                    proxies = {}
                    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
                    if proxy_url:
                        proxies = {"http": proxy_url, "https": proxy_url}
                        logger.debug(f"OCR.space (requests) использует прокси: {proxy_url}")
                    return requests.post(self.base_url, data=payload, timeout=30, proxies=proxies)
                
                resp = await asyncio.to_thread(sync_request)
                if resp.status_code != 200:
                    raise Exception(f"OCR.space API error {resp.status_code}: {resp.text}")
                result = resp.json()
            
            # Проверяем результат
            exit_code = result.get("OCRExitCode")
            if exit_code == 1:
                # Успешно распознано
                parsed_results = result.get("ParsedResults", [])
                if parsed_results:
                    text = parsed_results[0].get("ParsedText", "").strip()
                    if text:
                        logger.debug(f"OCR.space распознано {len(text)} символов")
                        return text
                
                logger.warning("OCR.space вернул пустой текст")
                return ""
            else:
                # Ошибка
                error_message = result.get("ErrorMessage", f"Exit code: {exit_code}")
                error_details = result.get("ErrorDetails", [])
                if error_details:
                    error_message += f" Details: {error_details}"
                
                # Специальная обработка для rate limit
                if "rate limit" in error_message.lower() or exit_code == 4:
                    raise Exception(f"OCR.space rate limit exceeded: {error_message}")
                
                raise Exception(f"OCR.space error: {error_message}")
                    
        except Exception as e:
            logger.error(f"OCR.space OCR error: {e}")
            raise


class EasyOCROCR:
    """OCR через EasyOCR API (бесплатно, без регистрации)"""
    
    def __init__(self):
        self.base_url = "https://api.easyocr.org/ocr"
        logger.info("EasyOCR API инициализирован (бесплатный, без регистрации)")
    
    async def recognize(self, image_bytes: bytes) -> str:
        """
        Распознаёт текст на изображении через EasyOCR API.
        
        Args:
            image_bytes: Байты изображения
        
        Returns:
            Распознанный текст
        """
        # Используем aiohttp если доступен, иначе requests
        use_aiohttp = aiohttp is not None
        
        if not use_aiohttp and requests is None:
            raise ImportError("Установите aiohttp или requests: pip install aiohttp или pip install requests")
        
        try:
            # Определяем расширение файла по первым байтам
            file_ext = "jpg"
            if image_bytes[:4] == b'\x89PNG':
                file_ext = "png"
            elif image_bytes[:2] == b'\xff\xd8':
                file_ext = "jpg"
            
            if use_aiohttp:
                # Асинхронный вариант через aiohttp
                data = aiohttp.FormData()
                data.add_field('file', image_bytes, filename=f'image.{file_ext}', content_type=f'image/{file_ext}')
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.base_url,
                        data=data,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as resp:
                        if resp.status != 200:
                            error_text = await resp.text()
                            raise Exception(f"EasyOCR API error {resp.status}: {error_text}")
                        
                        result = await resp.json()
            else:
                # Синхронный вариант через requests
                files = {'file': (f'image.{file_ext}', image_bytes, f'image/{file_ext}')}
                resp = requests.post(self.base_url, files=files, timeout=30)
                if resp.status_code != 200:
                    raise Exception(f"EasyOCR API error {resp.status_code}: {resp.text}")
                result = resp.json()
            
            # EasyOCR возвращает список текстовых блоков
            texts = []
            for item in result.get("text", []):
                text = item.get("text", "").strip()
                if text:
                    texts.append(text)
            
            recognized_text = "\n".join(texts)
            if recognized_text:
                logger.debug(f"EasyOCR распознано {len(recognized_text)} символов")
                return recognized_text
            
            logger.warning("EasyOCR вернул пустой текст")
            return ""
                    
        except Exception as e:
            logger.error(f"EasyOCR OCR error: {e}")
            raise
