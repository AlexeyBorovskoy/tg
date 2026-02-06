#!/usr/bin/env python3
"""
rag.py — RAG: эмбеддинги и запись в vec.embeddings (опционально)
"""

import hashlib
import logging
from typing import Optional

from config import Config

logger = logging.getLogger(__name__)

# Размерность OpenAI text-embedding-3-small
EMBEDDING_DIM = 1536


def _get_openai_client(config: Config):
    import openai
    return openai.OpenAI(api_key=config.openai_api_key)


def embed_texts(config: Config, texts: list[str], model: str = "text-embedding-3-small") -> list[list[float]]:
    """
    Получает эмбеддинги для списка текстов через OpenAI.
    Тексты длиннее ~8k токенов обрезаются (модель имеет лимит).
    """
    if not texts:
        return []
    client = _get_openai_client(config)
    # Ограничение длины для embedding API (примерно 8k токенов)
    truncated = [t[:6000] for t in texts]
    try:
        r = client.embeddings.create(input=truncated, model=model)
        return [e.embedding for e in r.data]
    except Exception as e:
        logger.error(f"OpenAI embeddings error: {e}")
        raise


def _embedding_to_str(embedding: list[float]) -> str:
    """Форматирует список float в строку для типа vector в PostgreSQL."""
    return "[" + ",".join(str(x) for x in embedding) + "]"


def insert_embeddings(
    db,
    peer_type: str,
    peer_id: int,
    source_type: str,
    source_id: int,
    content_text: str,
    embedding: list[float],
    chunk_index: int = 0,
) -> None:
    """Вставляет одну запись в vec.embeddings. Таблица и схема должны существовать."""
    vec_str = _embedding_to_str(embedding)
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO vec.embeddings (source_type, source_id, peer_type, peer_id, chunk_index, content_text, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
            ON CONFLICT (source_type, source_id, chunk_index) DO UPDATE SET
                content_text = EXCLUDED.content_text,
                embedding = EXCLUDED.embedding
            """,
            (source_type, source_id, peer_type, peer_id, chunk_index, content_text, vec_str),
        )


def index_digest_to_rag(config: Config, db, peer_type: str, peer_id: int, digest_id: int, digest_llm: str) -> None:
    """
    Индексирует текст дайджеста в vec.embeddings (один чанк).
    При ошибке (нет vec/embeddings) логирует и выходит.
    """
    if not digest_llm or not digest_llm.strip():
        return
    try:
        embs = embed_texts(config, [digest_llm[:6000]])
        if not embs:
            return
        insert_embeddings(
            db, peer_type, peer_id, "digest", digest_id, digest_llm[:6000], embs[0], chunk_index=0
        )
        logger.info(f"RAG: проиндексирован digest id={digest_id}")
    except Exception as e:
        logger.warning(f"RAG index digest: {e}")


def index_consolidated_doc_to_rag(
    config: Config, db, peer_type: str, peer_id: int, doc_path: str, content: str
) -> None:
    """
    Индексирует сводный документ в vec.embeddings (один чанк).
    source_id для consolidated_doc — hash пути или peer_id.
    """
    if not content or not content.strip():
        return
    source_id = abs(hash(doc_path)) % (2**31)
    try:
        embs = embed_texts(config, [content[:6000]])
        if not embs:
            return
        insert_embeddings(
            db, peer_type, peer_id, "consolidated_doc", source_id, content[:6000], embs[0], chunk_index=0
        )
        logger.info(f"RAG: проиндексирован consolidated_doc peer_id={peer_id}")
    except Exception as e:
        logger.warning(f"RAG index consolidated_doc: {e}")


def vec_schema_exists(db) -> bool:
    """Проверяет, есть ли схема vec и таблица vec.embeddings."""
    try:
        with db.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = 'vec' AND table_name = 'embeddings'"
            )
            return cur.fetchone() is not None
    except Exception:
        return False
