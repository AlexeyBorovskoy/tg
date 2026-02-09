#!/usr/bin/env python3
"""
Синхронизация промптов из папки prompts/ в таблицу prompt_library БД.
Запуск внутри контейнера web: docker exec tg_digest_web python /app/scripts/sync_prompts_db.py
"""

import os
import sys
from pathlib import Path

# Загрузка переменных окружения из контейнера
PGHOST = os.environ.get("PGHOST", "postgres")
PGPORT = int(os.environ.get("PGPORT", "5432"))
PGDATABASE = os.environ.get("PGDATABASE", "tg_digest")
PGUSER = os.environ.get("PGUSER", "tg_digest")
PGPASSWORD = os.environ.get("PGPASSWORD", "")

prompts_dir = Path(os.environ.get("PROMPTS_DIR", "/app/prompts"))
if not prompts_dir.exists():
    print(f"Папка промптов не найдена: {prompts_dir}", file=sys.stderr)
    sys.exit(1)

import psycopg2
from psycopg2.extras import RealDictCursor

conn = psycopg2.connect(
    host=PGHOST,
    port=PGPORT,
    database=PGDATABASE,
    user=PGUSER,
    password=PGPASSWORD,
)

synced = []
try:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        for ext in ("*.md", "*.txt"):
            for p in sorted(prompts_dir.glob(ext)):
                rel = f"prompts/{p.name}"
                try:
                    body = p.read_text(encoding="utf-8")
                except Exception as e:
                    print(f"Пропуск {p}: {e}", file=sys.stderr)
                    continue
                prompt_type = "consolidated" if "consolidated" in p.name.lower() else "digest"
                name = p.stem.replace("_", " ").replace("-", " ").title()
                cur.execute("""
                    INSERT INTO prompt_library (name, prompt_type, file_path, body)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (file_path) DO UPDATE SET
                        name = EXCLUDED.name,
                        prompt_type = EXCLUDED.prompt_type,
                        body = EXCLUDED.body,
                        updated_at = now()
                """, (name, prompt_type, rel, body))
                synced.append({"file_path": rel, "name": name, "prompt_type": prompt_type})
    conn.commit()
    print(f"Выгружено в prompt_library: {len(synced)} шаблонов")
    for s in synced:
        print(f"  - {s['file_path']} ({s['prompt_type']})")
except Exception as e:
    conn.rollback()
    print(f"Ошибка: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
finally:
    conn.close()
