#!/usr/bin/env python3
import os
import re
import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument

def env(name: str, default=None, required: bool=False):
    v = os.environ.get(name, default)
    if required and (v is None or str(v).strip() == ""):
        raise SystemExit(f"ERROR: env {name} is required")
    return v

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def safe_basename(name: str) -> str:
    name = name.strip().replace("\\", "_").replace("/", "_")
    name = re.sub(r"[^0-9A-Za-zА-Яа-я._-]+", "_", name)
    return name[:180] if len(name) > 180 else name

def detect_media_type(msg) -> str:
    m = msg.media
    if isinstance(m, MessageMediaPhoto):
        return "photo"
    if isinstance(m, MessageMediaDocument):
        # по mime/атрибутам можно уточнять, пока грубо
        return "file"
    return "other"

def main():
    # Telegram creds/session
    api_id = int(env("TG_API_ID", required=True))
    api_hash = env("TG_API_HASH", required=True)
    session = env("TG_SESSION", default=str(Path.home() / ".config" / "telethon" / "tg.session"))
    peer = env("TG_PEER", required=True)  # например: -1002700886173 или @channelname
    # window
    msg_id_from = int(env("MSG_ID_FROM", required=True))
    msg_id_to = int(env("MSG_ID_TO", required=True))

    repo_root = Path(env("REPO_DIR", default=str(Path.home() / "analysis-methodology")))
    media_root = Path(env("MEDIA_ROOT", default=str(repo_root / "docs" / "media")))
    peer_type = env("PEER_TYPE", default="channel")
    peer_id = int(env("PEER_ID", default="2700886173"))

    dry_run = env("DRY_RUN", default="0") == "1"

    # Postgres via psql
    db = env("PGDATABASE", default="rag")
    pghost = env("PGHOST", default="")
    pgport = env("PGPORT", default="")
    pguser = env("PGUSER", default="")
    pgenv = os.environ.copy()

    def psql(sql: str) -> str:
        # psql -X -qAt -P pager=off -d <db> -c "<sql>"
        import subprocess
        cmd = ["psql", "-X", "-qAt", "-P", "pager=off", "-d", db, "-c", sql]
        # PGHOST/PGPORT/PGUSER берутся из env автоматически
        r = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=pgenv)
        if r.returncode != 0:
            raise SystemExit(f"ERROR: psql failed rc={r.returncode}\nSTDERR:\n{r.stderr}\nSQL:\n{sql}")
        return r.stdout

    # ensure dirs
    media_root.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(session, api_id, api_hash)

    async def run():
        await client.start()
        ent = await client.get_entity(peer)

        count_total = 0
        count_saved = 0

        for mid in range(msg_id_from + 1, msg_id_to + 1):
            msg = await client.get_messages(ent, ids=mid)
            if not msg:
                continue
            if not msg.media:
                continue

            mtype = detect_media_type(msg)
            # layout: docs/media/<peer_type>_<peer_id>/<YYYY-MM-DD>/<msg_id>_<basename>
            dt = msg.date
            if dt is None:
                dt = datetime.now(timezone.utc)
            day = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")

            subdir = media_root / f"{peer_type}_{peer_id}" / day
            subdir.mkdir(parents=True, exist_ok=True)

            # имя файла
            fname = None
            if getattr(msg, "file", None) and getattr(msg.file, "name", None):
                fname = safe_basename(msg.file.name)
            else:
                # fallback
                ext = ""
                if isinstance(msg.media, MessageMediaPhoto):
                    ext = ".jpg"
                fname = f"media{ext}"
            out_path = subdir / f"{mid}_{fname}"
            rel_path = out_path.relative_to(repo_root)

            count_total += 1
            if dry_run:
                print(f"DRY: msg_id={mid} type={mtype} path={rel_path}")
                continue

            # download
            downloaded = await client.download_media(msg, file=str(out_path))
            if not downloaded:
                continue

            p = Path(downloaded)
            size_bytes = p.stat().st_size
            sha = sha256_file(p)

            mime = ""
            if getattr(msg, "file", None) and getattr(msg.file, "mime_type", None):
                mime = msg.file.mime_type

            # insert tg.media
            sql = f"""
            INSERT INTO tg.media(peer_type,peer_id,msg_id,media_type,local_path,sha256,mime,size_bytes)
            VALUES (
              '{peer_type}',
              {peer_id},
              {mid},
              '{mtype}',
              '{str(rel_path).replace("'", "''")}',
              '{sha}',
              {("NULL" if mime=="" else "'" + mime.replace("'", "''") + "'")},
              {size_bytes}
            )
            ON CONFLICT (peer_type,peer_id,msg_id,local_path) DO UPDATE
              SET sha256=EXCLUDED.sha256,
                  mime=EXCLUDED.mime,
                  size_bytes=EXCLUDED.size_bytes;
            """
            psql(sql)
            count_saved += 1
            print(f"OK: msg_id={mid} type={mtype} path={rel_path} size={size_bytes}")

        print(f"SUMMARY: total_media_msgs={count_total} saved={count_saved}")

    import asyncio
    asyncio.run(run())

if __name__ == "__main__":
    main()
