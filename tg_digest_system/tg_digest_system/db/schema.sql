-- ==============================================================================
-- TG Digest System — Схема базы данных
-- ==============================================================================
-- PostgreSQL 14+
-- Запуск: psql -d tg_digest -f schema.sql
-- ==============================================================================

BEGIN;

-- ------------------------------------------------------------------------------
-- Схемы
-- ------------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS tg;      -- Данные Telegram
CREATE SCHEMA IF NOT EXISTS rpt;     -- Отчёты и состояние
CREATE SCHEMA IF NOT EXISTS cfg;     -- Конфигурация (опционально)

-- ------------------------------------------------------------------------------
-- tg.channels — Справочник отслеживаемых каналов
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tg.channels (
    id              BIGSERIAL PRIMARY KEY,
    peer_id         BIGINT NOT NULL UNIQUE,
    peer_type       TEXT NOT NULL DEFAULT 'channel',
    name            TEXT NOT NULL,
    description     TEXT,
    prompt_file     TEXT NOT NULL,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE tg.channels IS 'Справочник Telegram каналов для мониторинга';

-- ------------------------------------------------------------------------------
-- tg.recipients — Получатели дайджестов
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tg.recipients (
    id              BIGSERIAL PRIMARY KEY,
    telegram_id     BIGINT NOT NULL,
    name            TEXT NOT NULL,
    role            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_recipients_tg_id ON tg.recipients(telegram_id);

COMMENT ON TABLE tg.recipients IS 'Справочник получателей дайджестов';

-- ------------------------------------------------------------------------------
-- tg.channel_recipients — Связь каналов и получателей
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tg.channel_recipients (
    channel_id      BIGINT NOT NULL REFERENCES tg.channels(id) ON DELETE CASCADE,
    recipient_id    BIGINT NOT NULL REFERENCES tg.recipients(id) ON DELETE CASCADE,
    send_file       BOOLEAN NOT NULL DEFAULT true,
    send_text       BOOLEAN NOT NULL DEFAULT true,
    PRIMARY KEY (channel_id, recipient_id)
);

COMMENT ON TABLE tg.channel_recipients IS 'Связь: какие получатели подписаны на какие каналы';

-- ------------------------------------------------------------------------------
-- tg.messages — Сообщения из Telegram
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tg.messages (
    id              BIGSERIAL PRIMARY KEY,
    peer_type       TEXT NOT NULL,
    peer_id         BIGINT NOT NULL,
    msg_id          BIGINT NOT NULL,
    dt              TIMESTAMPTZ NOT NULL,
    sender_id       BIGINT,
    sender_name     TEXT,
    text            TEXT,
    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_messages_peer_msg UNIQUE (peer_type, peer_id, msg_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_peer ON tg.messages(peer_type, peer_id, msg_id);
CREATE INDEX IF NOT EXISTS idx_messages_dt ON tg.messages(dt);
CREATE INDEX IF NOT EXISTS idx_messages_peer_dt ON tg.messages(peer_type, peer_id, dt);

COMMENT ON TABLE tg.messages IS 'Все сообщения из отслеживаемых каналов';

-- ------------------------------------------------------------------------------
-- tg.media — Медиафайлы (метаданные + binary в БД)
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tg.media (
    id              BIGSERIAL PRIMARY KEY,
    peer_type       TEXT NOT NULL,
    peer_id         BIGINT NOT NULL,
    msg_id          BIGINT NOT NULL,
    media_type      TEXT NOT NULL,           -- photo, video, file, voice, sticker
    file_name       TEXT,
    mime_type       TEXT,
    size_bytes      BIGINT,
    sha256          TEXT,
    file_data       BYTEA,                   -- Сам файл (опционально)
    local_path      TEXT,                    -- Путь к файлу (если не в БД)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_media_peer_msg UNIQUE (peer_type, peer_id, msg_id, file_name)
);

CREATE INDEX IF NOT EXISTS idx_media_peer_msg ON tg.media(peer_type, peer_id, msg_id);
CREATE INDEX IF NOT EXISTS idx_media_type ON tg.media(media_type);

COMMENT ON TABLE tg.media IS 'Медиафайлы из сообщений';

-- ------------------------------------------------------------------------------
-- tg.media_text — OCR и извлечённый текст из медиа
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tg.media_text (
    id              BIGSERIAL PRIMARY KEY,
    media_id        BIGINT REFERENCES tg.media(id) ON DELETE CASCADE,
    peer_type       TEXT NOT NULL,
    peer_id         BIGINT NOT NULL,
    msg_id          BIGINT NOT NULL,
    ocr_text        TEXT,
    ocr_model       TEXT,                    -- tesseract-5.x, easyocr, etc.
    ocr_confidence  REAL,                    -- 0.0-1.0
    caption_llm     TEXT,                    -- Описание от LLM (если нужно)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_media_text_peer_msg ON tg.media_text(peer_type, peer_id, msg_id);

COMMENT ON TABLE tg.media_text IS 'OCR-текст из изображений';

-- ------------------------------------------------------------------------------
-- rpt.report_state — Курсор обработки (последний обработанный msg_id)
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpt.report_state (
    id              BIGSERIAL PRIMARY KEY,
    peer_type       TEXT NOT NULL,
    peer_id         BIGINT NOT NULL,
    last_msg_id     BIGINT NOT NULL DEFAULT 0,
    last_poll_at    TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_report_state_peer UNIQUE (peer_type, peer_id)
);

COMMENT ON TABLE rpt.report_state IS 'Состояние обработки: до какого msg_id обработан каждый канал';

-- ------------------------------------------------------------------------------
-- rpt.digests — История сгенерированных дайджестов
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpt.digests (
    id              BIGSERIAL PRIMARY KEY,
    peer_type       TEXT NOT NULL,
    peer_id         BIGINT NOT NULL,
    msg_id_from     BIGINT NOT NULL,
    msg_id_to       BIGINT NOT NULL,
    digest_raw      TEXT,                    -- RAW digest (список сообщений)
    digest_llm      TEXT,                    -- LLM-обработанный дайджест
    llm_model       TEXT,
    llm_tokens_in   INTEGER,
    llm_tokens_out  INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_digests_peer ON rpt.digests(peer_type, peer_id);
CREATE INDEX IF NOT EXISTS idx_digests_created ON rpt.digests(created_at);

COMMENT ON TABLE rpt.digests IS 'История всех сгенерированных дайджестов';

-- ------------------------------------------------------------------------------
-- rpt.deliveries — Журнал отправки дайджестов
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpt.deliveries (
    id              BIGSERIAL PRIMARY KEY,
    digest_id       BIGINT REFERENCES rpt.digests(id),
    recipient_id    BIGINT REFERENCES tg.recipients(id),
    telegram_id     BIGINT NOT NULL,
    delivery_type   TEXT NOT NULL,           -- text, file, both
    status          TEXT NOT NULL,           -- sent, failed, pending
    error_message   TEXT,
    sent_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_deliveries_digest ON rpt.deliveries(digest_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_recipient ON rpt.deliveries(recipient_id);

COMMENT ON TABLE rpt.deliveries IS 'Журнал отправки дайджестов получателям';

-- ------------------------------------------------------------------------------
-- RAG: схема vec (pgvector) — единое хранилище для семантического поиска
-- ------------------------------------------------------------------------------
-- В vec хранятся эмбеддинги всех текстов: сообщения, OCR, дайджесты, сводные доки.
-- Расширение: CREATE EXTENSION vector; (pgvector)
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS vec;

CREATE TABLE IF NOT EXISTS vec.embeddings (
    id              BIGSERIAL PRIMARY KEY,
    source_type     TEXT NOT NULL,           -- message | media_text | digest | consolidated_doc
    source_id       BIGINT NOT NULL,         -- id сообщения / digest / документа
    peer_type       TEXT NOT NULL,
    peer_id         BIGINT NOT NULL,
    chunk_index     INTEGER NOT NULL DEFAULT 0,
    content_text    TEXT NOT NULL,
    embedding       vector(1536),            -- OpenAI text-embedding-3-small; иначе изменить размерность
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_type, source_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_peer ON vec.embeddings(peer_type, peer_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_source ON vec.embeddings(source_type, source_id);
-- Векторный индекс создавать после появления данных (ivfflat требует строк):
-- CREATE INDEX idx_embeddings_vector ON vec.embeddings
--   USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

COMMENT ON TABLE vec.embeddings IS 'RAG: эмбеддинги сообщений, OCR, дайджестов, сводных документов';

-- Сводные документы по чату (опционально: версии для истории)
CREATE TABLE IF NOT EXISTS vec.documents (
    id              BIGSERIAL PRIMARY KEY,
    peer_type       TEXT NOT NULL,
    peer_id         BIGINT NOT NULL,
    doc_path        TEXT NOT NULL,           -- путь в docs/reference/
    content_hash    TEXT,                    -- для дедупликации
    content_preview TEXT,                    -- первые N символов
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (peer_type, peer_id)
);

COMMENT ON TABLE vec.documents IS 'Метаданные сводных инженерных документов по чатам';

-- ------------------------------------------------------------------------------
-- Функция обновления updated_at
-- ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Триггеры
DROP TRIGGER IF EXISTS trg_channels_updated ON tg.channels;
CREATE TRIGGER trg_channels_updated
    BEFORE UPDATE ON tg.channels
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trg_report_state_updated ON rpt.report_state;
CREATE TRIGGER trg_report_state_updated
    BEFORE UPDATE ON rpt.report_state
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trg_media_text_updated ON tg.media_text;
CREATE TRIGGER trg_media_text_updated
    BEFORE UPDATE ON tg.media_text
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ------------------------------------------------------------------------------
-- Начальные данные для тестирования
-- ------------------------------------------------------------------------------
-- INSERT INTO rpt.report_state (peer_type, peer_id, last_msg_id)
-- VALUES ('channel', 2700886173, 0)
-- ON CONFLICT DO NOTHING;

COMMIT;

-- ==============================================================================
-- Проверка
-- ==============================================================================
-- \dt tg.*
-- \dt rpt.*
-- \dt vec.*
