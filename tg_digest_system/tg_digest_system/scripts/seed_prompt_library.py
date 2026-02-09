#!/usr/bin/env python3
"""
Однократная выгрузка промптов из папки prompts/ в таблицу prompt_library (общий доступ).
Запуск после применения миграции 005 к БД.

Использование:
  PROMPTS_DIR=../prompts PGHOST=localhost PGDATABASE=tg_digest PGUSER=tg_digest PGPASSWORD=... python seed_prompt_library.py
  или из каталога scripts с загруженным secrets.env:
  python seed_prompt_library.py
"""

import os
import sys
from pathlib import Path

# Загрузка secrets.env
for _p in (
    Path(__file__).resolve().parent.parent / "docker" / "secrets.env",
    Path(__file__).resolve().parent.parent / "secrets.env",
    Path.cwd() / "secrets.env",
    Path.cwd() / "docker" / "secrets.env",
):
    if _p.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(_p, override=False)
            break
        except ImportError:
            break

import psycopg2
from psycopg2.extras import RealDictCursor


def main():
    prompts_dir = Path(os.environ.get("PROMPTS_DIR", Path(__file__).resolve().parent.parent / "prompts"))
    if not prompts_dir.exists():
        print(f"Папка промптов не найдена: {prompts_dir}", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5432")),
        database=os.environ.get("PGDATABASE", "tg_digest"),
        user=os.environ.get("PGUSER", "tg_digest"),
        password=os.environ.get("PGPASSWORD", ""),
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
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
