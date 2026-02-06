#!/usr/bin/env python3
"""
llm.py — Интеграция с OpenAI API для генерации дайджестов
"""

import re
import logging
import time
import os
from typing import Optional, Tuple

import openai

from config import Config, Channel, get_prompt, get_consolidated_prompt

logger = logging.getLogger(__name__)


class LLMService:
    """Сервис генерации дайджестов через OpenAI"""
    
    def __init__(self, config: Config):
        self.config = config
        kwargs = {"api_key": config.openai_api_key}
        if config.openai_base_url:
            kwargs["base_url"] = config.openai_base_url
        
        # Настройка прокси для доступа через VPN
        proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        if proxy_url:
            try:
                import httpx
                # Создаем HTTP клиент с прокси
                if proxy_url.startswith("socks5://"):
                    # Для SOCKS5 нужен socksio
                    try:
                        import socksio
                        http_client = httpx.Client(
                            proxy=proxy_url,
                            timeout=300.0,
                        )
                    except ImportError:
                        logger.warning("socksio не установлен, прокси может не работать. Установите: pip install httpx[socks]")
                        http_client = httpx.Client(timeout=300.0)
                else:
                    http_client = httpx.Client(proxy=proxy_url, timeout=300.0)
                
                kwargs["http_client"] = http_client
                logger.info(f"OpenAI клиент настроен с прокси: {proxy_url}")
            except ImportError:
                logger.warning("httpx не установлен, прокси не будет использован")
            except Exception as e:
                logger.warning(f"Ошибка настройки прокси для OpenAI: {e}")
        
        self.client = openai.OpenAI(**kwargs)
    
    def generate_digest(
        self,
        channel: Channel,
        raw_digest: str,
        ocr_texts: Optional[list[dict]] = None,
    ) -> Tuple[str, int, int]:
        """
        Генерирует LLM-дайджест.
        
        Returns:
            Tuple[digest_text, tokens_in, tokens_out]
        """
        try:
            system_prompt = get_prompt(self.config, channel)
        except FileNotFoundError:
            system_prompt = self._get_fallback_prompt()
        
        user_content = self._build_user_prompt(raw_digest, ocr_texts)
        sys_len = len(system_prompt)
        user_len = len(user_content)

        logger.info(
            "LLM request (digest): model=%s max_tokens=%s channel_id=%s step=digest "
            "prompt_len=%s user_len=%s",
            self.config.openai_model,
            self.config.openai_max_tokens,
            channel.id,
            sys_len,
            user_len,
        )
        t0 = time.monotonic()
        try:
            response = self.client.chat.completions.create(
                model=self.config.openai_model,
                temperature=self.config.openai_temperature,
                max_tokens=self.config.openai_max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            )
            duration = time.monotonic() - t0
            digest_text = response.choices[0].message.content
            tokens_in = response.usage.prompt_tokens
            tokens_out = response.usage.completion_tokens
            digest_text = self._postprocess(digest_text)

            logger.info(
                "LLM response (digest): tokens_in=%s tokens_out=%s response_len=%s duration_sec=%.2f",
                tokens_in,
                tokens_out,
                len(digest_text),
                duration,
            )
            return digest_text, tokens_in, tokens_out

        except openai.APIError as e:
            duration = time.monotonic() - t0
            logger.error(
                "LLM error (digest): channel_id=%s model=%s type=%s msg=%s duration_sec=%.2f",
                channel.id,
                self.config.openai_model,
                type(e).__name__,
                str(e),
                duration,
            )
            logger.exception("LLM digest full traceback")
            raise
        except Exception as e:
            duration = time.monotonic() - t0
            logger.error(
                "LLM unexpected error (digest): channel_id=%s type=%s msg=%s duration_sec=%.2f",
                channel.id,
                type(e).__name__,
                str(e),
                duration,
            )
            logger.exception("LLM digest full traceback")
            raise
    
    def _build_user_prompt(self, raw_digest: str, ocr_texts: Optional[list[dict]]) -> str:
        """Формирует запрос пользователя"""
        # Проверяем, есть ли сообщения (по количеству строк с msg_id)
        has_messages = "msg_id=" in raw_digest and "**Новых сообщений за день не было.**" not in raw_digest
        
        if has_messages:
            parts = [
                "Сформируй управленческий дайджест по следующим сообщениям.\n",
                "RAW-данные:\n\n",
                raw_digest,
            ]
        else:
            parts = [
                "Сформируй управленческий дайджест за день.\n",
                "ВАЖНО: Если новых сообщений за день не было, укажи это в дайджесте и кратко опиши текущий статус проекта на основе предыдущих данных.\n",
                "RAW-данные:\n\n",
                raw_digest,
            ]
        
        if ocr_texts:
            parts.append("\n\nOCR-текст из изображений:\n")
            for item in ocr_texts:
                text = item.get("ocr_text", "")[:500]
                parts.append(f"- msg_id={item['msg_id']}: {text}\n")
        
        return "".join(parts)
    
    def _postprocess(self, text: str) -> str:
        """Постобработка текста дайджеста"""
        text = text.strip()
        # Удаляем висячие строки вида "- msg_id=123:"
        text = re.sub(r"^(?:-\s*)?msg_id=\d+\s*:?\s*$", "", text, flags=re.MULTILINE)
        # Убираем множественные пустые строки
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    
    def _get_fallback_prompt(self) -> str:
        """Запасной промпт"""
        return """Ты технический аналитик. Сформируй краткий дайджест по сообщениям.

Формат:
## Решения/Задачи
## Риски/Проблемы  
## Следующие шаги

Сохраняй ссылки msg_id=XXXX. Без воды, только факты."""

    def generate_consolidated_doc(
        self,
        channel: Channel,
        messages: list[dict],
        ocr_texts: list[dict],
        recent_digests: list[dict],
        previous_doc_content: str,
    ) -> Tuple[str, str, int, int]:
        """
        Генерирует единый сводный инженерный документ по чату (перезапись целиком).
        В конце ответа LLM должен вывести строку ИЗМЕНЕНИЕ_ДЛЯ_УВЕДОМЛЕНИЯ: ... для рассылки получателям.

        Args:
            channel: канал
            messages: сообщения из БД (msg_id, dt, sender_name, text)
            ocr_texts: OCR по чату (msg_id, ocr_text)
            recent_digests: последние дайджесты (digest_llm, msg_id_from, msg_id_to)
            previous_doc_content: текущее содержимое файла сводного документа (может быть пустым)

        Returns:
            Tuple[doc_content, changes_summary, tokens_in, tokens_out]
        """
        try:
            system_prompt = get_consolidated_prompt(self.config, channel)
        except FileNotFoundError:
            system_prompt = """Ты формируешь сводный инженерный документ по чату.
Структура: 1) Текущее состояние системы, 2) Задачи на доработку ПО, 3) API и интеграции,
4) Известные проблемы и технические риски, 5) Следующие шаги (инженерные).
Перезапиши документ целиком по новым данным. Сохраняй msg_id для трассировки.
В самом конце ответа добавь одну строку: ИЗМЕНЕНИЕ_ДЛЯ_УВЕДОМЛЕНИЯ: кратко (1–2 предложения) что изменилось в этом обновлении."""

        user_content = self._build_consolidated_user_prompt(
            channel, messages, ocr_texts, recent_digests, previous_doc_content
        )
        user_content += "\n\nВ конце ответа обязательно добавь одну строку: ИЗМЕНЕНИЕ_ДЛЯ_УВЕДОМЛЕНИЯ: кратко (1–2 предложения) что изменилось в этом обновлении."

        sys_len = len(system_prompt)
        user_len = len(user_content)
        logger.info(
            "LLM request (consolidated_doc): model=%s max_tokens=%s channel_id=%s step=consolidated_doc "
            "prompt_len=%s user_len=%s messages_count=%s",
            self.config.openai_model,
            self.config.openai_max_tokens,
            channel.id,
            sys_len,
            user_len,
            len(messages),
        )
        t0 = time.monotonic()
        try:
            response = self.client.chat.completions.create(
                model=self.config.openai_model,
                temperature=self.config.openai_temperature,
                max_tokens=self.config.openai_max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                timeout=300.0,
            )
            duration = time.monotonic() - t0
            doc_text = response.choices[0].message.content
            if doc_text is None:
                logger.error("LLM вернул None для consolidated_doc")
                raise ValueError("LLM вернул пустой ответ для сводного документа")
            tokens_in = response.usage.prompt_tokens if response.usage else 0
            tokens_out = response.usage.completion_tokens if response.usage else 0
            doc_text = doc_text.strip()

            changes_summary = ""
            marker = "ИЗМЕНЕНИЕ_ДЛЯ_УВЕДОМЛЕНИЯ:"
            if marker in doc_text:
                idx = doc_text.find(marker)
                rest = doc_text[idx + len(marker) :].strip()
                first_line = rest.split("\n")[0].strip()
                if first_line:
                    changes_summary = first_line
                doc_text = doc_text[:idx].strip()

            logger.info(
                "LLM response (consolidated_doc): tokens_in=%s tokens_out=%s doc_len=%s changes_len=%s duration_sec=%.2f",
                tokens_in,
                tokens_out,
                len(doc_text),
                len(changes_summary),
                duration,
            )
            return doc_text, changes_summary, tokens_in, tokens_out
        except openai.APIError as e:
            duration = time.monotonic() - t0
            logger.error(
                "LLM error (consolidated_doc): channel_id=%s model=%s type=%s msg=%s duration_sec=%.2f",
                channel.id,
                self.config.openai_model,
                type(e).__name__,
                str(e),
                duration,
            )
            logger.exception("LLM consolidated_doc full traceback")
            raise
        except Exception as e:
            duration = time.monotonic() - t0
            logger.error(
                "LLM unexpected error (consolidated_doc): channel_id=%s type=%s msg=%s duration_sec=%.2f",
                channel.id,
                type(e).__name__,
                str(e),
                duration,
            )
            logger.exception("LLM consolidated_doc full traceback")
            raise

    def _build_consolidated_user_prompt(
        self,
        channel: Channel,
        messages: list[dict],
        ocr_texts: list[dict],
        recent_digests: list[dict],
        previous_doc_content: str,
    ) -> str:
        """Собирает пользовательский запрос для сводного документа"""
        parts = [
            f"Сформируй единый сводный инженерный документ по чату «{channel.name}» (peer_id={channel.id}).\n",
            "Перезапиши документ целиком по данным ниже. Предыдущая версия документа — в конце; замени её новой версией.\n\n",
        ]

        # Ограничение размера промпта для быстрого ответа API (~3–5 мин)
        MAX_USER_CHARS = 95_000
        MSG_TEXT_TRUNCATE = 280
        OCR_TEXT_TRUNCATE = 350
        total_chars = 0

        parts.append("## Сообщения чата (из БД tg.messages) — последние для анализа\n\n")
        for msg in messages:
            if total_chars >= MAX_USER_CHARS:
                parts.append(f"\n... (ещё сообщений не показано, всего {len(messages)})\n\n")
                break
            dt = msg["dt"].strftime("%Y-%m-%d %H:%M:%S") if msg.get("dt") else "?"
            sender = msg.get("sender_name") or "[NO_SENDER]"
            text = (msg.get("text") or "[EMPTY]").replace("\n", " ").strip()
            if len(text) > MSG_TEXT_TRUNCATE:
                text = text[:MSG_TEXT_TRUNCATE] + "..."
            line = f"- **{dt}** `msg_id={msg['msg_id']}` **{sender}**: {text}\n"
            parts.append(line)
            total_chars += len(line)
        parts.append("\n")

        if ocr_texts and total_chars < MAX_USER_CHARS:
            parts.append("## OCR-текст из изображений (tg.media_text)\n\n")
            for item in ocr_texts:
                if total_chars >= MAX_USER_CHARS:
                    parts.append(f"\n... (ещё OCR не показано)\n\n")
                    break
                text = (item.get("ocr_text") or "").replace("\n", " ").strip()
                if len(text) > OCR_TEXT_TRUNCATE:
                    text = text[:OCR_TEXT_TRUNCATE] + "..."
                line = f"- msg_id={item['msg_id']}: {text}\n"
                parts.append(line)
                total_chars += len(line)
            parts.append("\n")

        if recent_digests:
            parts.append("## Последние дайджесты (rpt.digests)\n\n")
            for d in recent_digests:
                llm = (d.get("digest_llm") or "")[:600]
                parts.append(f"Окно msg_id ({d.get('msg_id_from')}, {d.get('msg_id_to')}]:\n{llm}\n\n")
            parts.append("\n")

        if previous_doc_content:
            parts.append("## Текущая версия сводного документа (перезапиши целиком)\n\n")
            parts.append(previous_doc_content[:8000])
            parts.append("\n\n---\nВыше — предыдущая версия. Сформируй новую полную версию документа.")

        return "".join(parts)
