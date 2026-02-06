#!/usr/bin/env python3
import os
import re
import subprocess
from pathlib import Path
import psycopg2

RE_MEDIA_PATH = re.compile(r"docs/media/(?P<peer_type>[^_]+)_(?P<peer_id>-?\d+)/(?P<date>\d{4}-\d{2}-\d{2})/(?P<msg_id>\d+).*\.(jpg|jpeg|png)$", re.IGNORECASE)

def run_ocr(img_path: Path) -> str:
    # tesseract stdin->stdout: tesseract <img> stdout -l rus+eng
    cmd = ["tesseract", str(img_path), "stdout", "-l", "rus+eng", "--psm", "6"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # tesseract sometimes writes warnings to stderr; treat OCR text as stdout
    text = p.stdout or ""
    # normalize
    text = text.replace("\r\n", "\n").strip()
    # hard limit to keep DB light
    if len(text) > 8000:
        text = text[:8000] + "\n[...TRUNCATED...]"
    return text

def parse_meta(rel_path: str):
    m = RE_MEDIA_PATH.search(rel_path)
    if not m:
        return None
    return (m.group("peer_type"), int(m.group("peer_id")), int(m.group("msg_id")))

def main():
    repo_dir = Path(os.environ.get("REPO_DIR", ".")).resolve()
    limit = int(os.environ.get("OCR_LIMIT", "5"))
    dry_run = os.environ.get("DRY_RUN", "0") == "1"

    dsn = {
        "host": os.environ.get("PGHOST"),
        "port": os.environ.get("PGPORT", "5432"),
        "dbname": os.environ.get("PGDATABASE", "rag"),
        "user": os.environ.get("PGUSER"),
        "password": os.environ.get("PGPASSWORD"),
    }

    conn = psycopg2.connect(**{k:v for k,v in dsn.items() if v})
    conn.autocommit = False

    with conn, conn.cursor() as cur:
        cur.execute("""
          SELECT local_path
          FROM tg.media
          WHERE media_type='photo'
          ORDER BY created_at DESC
          LIMIT %s
        """, (limit,))
        rows = cur.fetchall()

        print(f"INFO: selected={len(rows)} limit={limit} dry_run={dry_run}")
        done = 0

        for (local_path,) in rows:
            rel = local_path
            meta = parse_meta(rel)
            if not meta:
                print(f"WARN: skip (unparsed path): {rel}")
                continue

            peer_type, peer_id, msg_id = meta
            img = repo_dir / rel
            if not img.exists():
                print(f"WARN: file missing: {img}")
                continue

            text = run_ocr(img)
            # make a short preview for console
            preview = (text[:200].replace("\n", " ") + ("..." if len(text) > 200 else ""))
            print(f"OK: msg_id={msg_id} file={img.name} ocr_len={len(text)} preview='{preview}'")

            if not dry_run:
                cur.execute("""
                  INSERT INTO tg.media_text(peer_type, peer_id, msg_id, local_path, ocr_text, model)
                  VALUES (%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (peer_type, peer_id, msg_id, local_path)
                  DO UPDATE SET ocr_text=EXCLUDED.ocr_text, model=EXCLUDED.model, updated_at=now()
                """, (peer_type, peer_id, msg_id, rel, text, "tesseract-5.3.4 rus+eng"))
                done += 1

        if dry_run:
            conn.rollback()
            print("DRY_RUN: rollback")
        else:
            conn.commit()
            print(f"DONE: upserted={done}")

if __name__ == "__main__":
    main()
