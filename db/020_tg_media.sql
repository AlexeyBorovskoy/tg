-- 020_tg_media.sql
-- Media tables for Telegram attachments and derived text (OCR/LLM)

BEGIN;

CREATE TABLE IF NOT EXISTS tg.media (
  peer_type   text        NOT NULL,
  peer_id     bigint      NOT NULL,
  msg_id      bigint      NOT NULL,
  media_type  text        NOT NULL, -- photo|video|file|voice|sticker|other
  local_path  text        NOT NULL, -- path relative to repo root (recommended)
  sha256      text        NULL,
  mime        text        NULL,
  size_bytes  bigint      NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (peer_type, peer_id, msg_id, local_path)
);

CREATE INDEX IF NOT EXISTS ix_tg_media_peer_msg
  ON tg.media(peer_type, peer_id, msg_id);

CREATE TABLE IF NOT EXISTS tg.media_text (
  peer_type    text        NOT NULL,
  peer_id      bigint      NOT NULL,
  msg_id       bigint      NOT NULL,
  local_path   text        NOT NULL,
  ocr_text     text        NULL,
  caption_llm  text        NULL,
  model        text        NULL,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (peer_type, peer_id, msg_id, local_path)
);

CREATE INDEX IF NOT EXISTS ix_tg_media_text_peer_msg
  ON tg.media_text(peer_type, peer_id, msg_id);

COMMIT;
