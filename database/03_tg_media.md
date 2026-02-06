# PostgreSQL: tg.media / tg.media_text

## 1. Назначение
Хранение сведений о медиафайлах Telegram, скачанных на VM, с привязкой к `(peer_type, peer_id, msg_id)`.
Дальнейшая обработка: OCR (для изображений/скриншотов) и LLM-caption, затем включение результатов в LLM-дайджест.

## 2. Таблица tg.media

### 2.1. DDL (референс)
```sql
CREATE TABLE IF NOT EXISTS tg.media (
  peer_type   text        NOT NULL,
  peer_id     bigint      NOT NULL,
  msg_id      bigint      NOT NULL,
  media_type  text        NOT NULL, -- photo|video|file|voice|sticker|other
  local_path  text        NOT NULL, -- путь на VM (рекомендуется относительный от корня репо)
  sha256      text        NULL,
  mime        text        NULL,
  size_bytes  bigint      NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (peer_type, peer_id, msg_id, local_path)
);

CREATE INDEX IF NOT EXISTS ix_tg_media_peer_msg
  ON tg.media(peer_type, peer_id, msg_id);
