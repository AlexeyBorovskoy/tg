#!/usr/bin/env python3
import os
import sys
import hashlib
import mimetypes
from pathlib import Path
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.tl.types import Message

import psycopg2


def eprint(*a):
    print(*a, file=sys.stderr)


def env_required(name: str) -> str:
    v = os.environ.get(name, "")
    if not v:
        raise SystemExit(f"ERROR: env {name} is empty")
    return v


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def db_conn():
    # Используем стандартные PG* переменные окружения
    return psycopg2.connect(
        host=os.environ.get("PGHOST"),
        port=os.environ.get("PGPORT"),
        dbname=os.environ.get("PGDATABASE", "rag"),
        user=os.environ.get("PGUSER"),
        password=os.environ.get("PGPASSWORD"),
    )


def upsert_media(cur, peer_type: str, peer_id: int, msg_id: int, media_type: str, rel_path: str,
                 sha256: str, mime: str, size_bytes: int):
    cur.execute(
        """
        INSERT INTO tg.media(peer_type, peer_id, msg_id, media_type, local_path, sha256, mime, size_bytes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (peer_type, peer_id, msg_id, local_path)
        DO UPDATE SET sha256=EXCLUDED.sha256, mime=EXCLUDED.mime, size_bytes=EXCLUDED.size_bytes;
        """,
        (peer_type, peer_id, msg_id, media_type, rel_path, sha256, mime, size_bytes)
    )


def detect_media_type(m: Message) -> str:
    if m.photo:
        return "photo"
    if m.video:
        return "video"
    if m.voice:
        return "voice"
    if m.document:
        return "file"
    if m.sticker:
        return "sticker"
    return "other"


async def main():
    # Telegram
    api_id = int(env_required("TG_API_ID"))
    api_hash = env_required("TG_API_HASH")
    session_file = env_required("TG_SESSION_FILE")
    target_chat_id = int(env_required("TG_TARGET_CHAT_ID"))

    # Window
    peer_type = os.environ.get("PEER_TYPE", "channel")
    peer_id = int(os.environ.get("PEER_ID", str(target_chat_id)))
    msg_id_from = int(env_required("MSG_ID_FROM"))
    msg_id_to = int(env_required("MSG_ID_TO"))

    # Storage
    repo_root = Path(env_required("REPO_DIR")).resolve()
    media_root = (repo_root / "docs" / "media").resolve()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = media_root / f"{peer_type}_{peer_id}" / day
    out_dir.mkdir(parents=True, exist_ok=True)

    dry_run = os.environ.get("DRY_RUN", "0") == "1"

    # DB
    conn = db_conn()
    conn.autocommit = False

    client = TelegramClient(session_file, api_id, api_hash)
    await client.start()

    eprint(f"INFO: peer={peer_type} {peer_id}, window=({msg_id_from},{msg_id_to}] dry_run={dry_run}")
    eprint(f"INFO: media_root={media_root}")

    saved = 0
    scanned = 0

    try:
        with conn.cursor() as cur:
            # Идём по сообщениям: msg_id_from+1 .. msg_id_to
            for mid in range(msg_id_from + 1, msg_id_to + 1):
                m = await client.get_messages(target_chat_id, ids=mid)
                scanned += 1
                if not m or not getattr(m, "media", None):
                    continue

                media_type = detect_media_type(m)

                # имя файла: <msg_id>_<basename>
                # Telethon сам проставит расширение по типу
                base = f"{mid}"
                out_path = out_dir / f"{base}"
                rel_path = str(out_path.relative_to(repo_root))

                if dry_run:
                    # только пишем “виртуально” (без скачивания) — для контроля окна
                    upsert_media(cur, peer_type, peer_id, mid, media_type, rel_path, None, None, None)
                    saved += 1
                    continue

                real_path = await client.download_media(m, file=str(out_path))
                if not real_path:
                    continue

                p = Path(real_path)
                size_bytes = p.stat().st_size
                mime, _ = mimetypes.guess_type(str(p))
                mime = mime or ""

                h = sha256_file(p)
                rel_path_real = str(p.resolve().relative_to(repo_root))

                upsert_media(cur, peer_type, peer_id, mid, media_type, rel_path_real, h, mime, size_bytes)
                saved += 1

            conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        await client.disconnect()
        conn.close()

    eprint(f"OK: scanned={scanned} saved_media_rows={saved}")


if __name__ == "__main__":
    try:
        import telethon  # noqa
    except Exception as ex:
        raise SystemExit("ERROR: telethon not installed in current python") from ex
    try:
        import psycopg2  # noqa
    except Exception as ex:
        raise SystemExit("ERROR: psycopg2 not installed in current python") from ex

    import asyncio
    asyncio.run(main())
