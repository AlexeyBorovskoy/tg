-- Миграция 008: Настройки доставки дайджестов по каналу (важный / ознакомительный)
-- Дата: 2026-02-06
-- Описание: Колонки в web_channels для настройки отправки дайджеста (полный дайджест или кратко, с файлом или без)

ALTER TABLE web_channels
  ADD COLUMN IF NOT EXISTS delivery_importance TEXT DEFAULT 'important' CHECK (delivery_importance IN ('important', 'informational')),
  ADD COLUMN IF NOT EXISTS delivery_send_file BOOLEAN DEFAULT true,
  ADD COLUMN IF NOT EXISTS delivery_send_text BOOLEAN DEFAULT true,
  ADD COLUMN IF NOT EXISTS delivery_text_max_chars INTEGER,
  ADD COLUMN IF NOT EXISTS delivery_summary_only BOOLEAN DEFAULT false;

COMMENT ON COLUMN web_channels.delivery_importance IS 'important = полный дайджест (текст + файл), informational = ознакомительный';
COMMENT ON COLUMN web_channels.delivery_send_file IS 'Отправлять файл с полным дайджестом';
COMMENT ON COLUMN web_channels.delivery_send_text IS 'Отправлять текст дайджеста в сообщении';
COMMENT ON COLUMN web_channels.delivery_text_max_chars IS 'Ограничение длины текста в сообщении (для ознакомительных)';
COMMENT ON COLUMN web_channels.delivery_summary_only IS 'Только краткое резюме в сообщении (с учётом text_max_chars)';
