#!/usr/bin/env python3
"""
web_api.py — FastAPI веб-приложение для управления каналами
"""

import os
import json
import logging
import asyncio
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from fastapi import FastAPI, HTTPException, Depends, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import psycopg2
from psycopg2.extras import RealDictCursor

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="TG Digest Web Interface", version="1.0.0")

# Пути
BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Создаём директории если их нет
TEMPLATES_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Подключение к БД
def get_db():
    """Получает подключение к БД"""
    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5432")),
        database=os.environ.get("PGDATABASE", "tg_digest"),
        user=os.environ.get("PGUSER", "tg_digest"),
        password=os.environ.get("PGPASSWORD", ""),
    )
    try:
        yield conn
    finally:
        conn.close()


# Pydantic модели
class ChannelCreate(BaseModel):
    telegram_chat_id: int = Field(..., description="Telegram ID чата")
    name: Optional[str] = Field(None, description="Название чата")
    recipient_telegram_id: int = Field(..., description="Telegram ID получателя дайджестов")
    recipient_name: Optional[str] = Field(None, description="Имя получателя")
    prompt_file: str = Field("prompts/digest_management.md", description="Файл промпта")
    poll_interval_minutes: int = Field(60, description="Интервал опроса в минутах")


class ChannelResponse(BaseModel):
    id: int
    telegram_chat_id: int
    name: str
    description: Optional[str]
    peer_type: str
    enabled: bool
    recipient_telegram_id: int
    recipient_name: Optional[str]
    created_at: datetime
    last_digest_at: Optional[datetime] = None
    total_messages: int = 0


class UserCreate(BaseModel):
    telegram_id: int = Field(..., description="Telegram ID пользователя")
    name: Optional[str] = None


# Вспомогательные функции
def get_or_create_user(conn, telegram_id: int, name: Optional[str] = None) -> int:
    """Получает или создаёт пользователя"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Проверяем существование
        cur.execute("SELECT id FROM users WHERE telegram_id = %s", (telegram_id,))
        row = cur.fetchone()
        if row:
            return row['id']
        
        # Создаём нового
        cur.execute(
            "INSERT INTO users (telegram_id, name) VALUES (%s, %s) RETURNING id",
            (telegram_id, name or f"User {telegram_id}")
        )
        user_id = cur.fetchone()['id']
        conn.commit()
        return user_id


async def check_chat_access(chat_id: int) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Проверяет доступ к чату через системную Telethon сессию.
    Возвращает (доступ_есть, peer_type, название)
    
    На первом этапе поддерживаются только чаты где система присутствует.
    """
    try:
        from telethon import TelegramClient
        from telethon.tl.types import Channel, Chat
        from telethon.errors import UsernameNotOccupiedError, ChannelPrivateError
        
        api_id = int(os.environ.get("TG_API_ID", "0"))
        api_hash = os.environ.get("TG_API_HASH", "")
        session_file = os.environ.get("TG_SESSION_FILE", "")
        
        if not api_id or not api_hash or not session_file:
            logger.error("Telegram credentials не настроены")
            return False, None, None
        
        client = TelegramClient(session_file, api_id, api_hash)
        await client.start()
        try:
            entity = await client.get_entity(chat_id)
            
            if isinstance(entity, Channel):
                peer_type = "channel" if entity.broadcast else "group"
            elif isinstance(entity, Chat):
                peer_type = "group"
            else:
                peer_type = "group"
            
            name = getattr(entity, 'title', None) or getattr(entity, 'first_name', 'Unknown')
            await client.disconnect()
            logger.info(f"Доступ к чату {chat_id} ({name}) подтверждён")
            return True, peer_type, name
            
        except (UsernameNotOccupiedError, ChannelPrivateError, ValueError) as e:
            logger.warning(f"Нет доступа к чату {chat_id}: {e}")
            await client.disconnect()
            return False, None, None
        except Exception as e:
            logger.error(f"Ошибка проверки доступа к чату {chat_id}: {e}")
            await client.disconnect()
            return False, None, None
    except Exception as e:
        logger.error(f"Ошибка инициализации Telethon: {e}")
        return False, None, None


# API Endpoints
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Главная страница с формой добавления чата"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/channels", response_class=HTMLResponse)
async def channels_page(request: Request, user_telegram_id: int = 499412926):
    """Страница со списком каналов пользователя"""
    return templates.TemplateResponse("channels.html", {
        "request": request,
        "user_telegram_id": user_telegram_id
    })


@app.get("/instructions", response_class=HTMLResponse)
async def instructions_page(request: Request):
    """Страница с инструкциями для новых пользователей"""
    return templates.TemplateResponse("instructions.html", {"request": request})


@app.get("/prompts", response_class=HTMLResponse)
async def prompts_page(request: Request):
    """Страница редактирования промптов канала"""
    return templates.TemplateResponse("prompts_v2.html", {"request": request})


@app.post("/api/users", response_class=JSONResponse)
async def create_user(user: UserCreate, db=Depends(get_db)):
    """Создаёт или получает пользователя"""
    try:
        user_id = get_or_create_user(db, user.telegram_id, user.name)
        return {"user_id": user_id, "telegram_id": user.telegram_id}
    except Exception as e:
        logger.error(f"Ошибка создания пользователя: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channels", response_class=JSONResponse)
async def list_channels(user_telegram_id: int, db=Depends(get_db)):
    """Возвращает список каналов пользователя"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            # Получаем каналы
            cur.execute("""
                SELECT 
                    wc.*,
                    (SELECT COUNT(*) FROM tg.messages m 
                     WHERE m.user_id = wc.user_id 
                     AND m.peer_id = wc.telegram_chat_id) as total_messages,
                    (SELECT MAX(created_at) FROM rpt.digests d 
                     WHERE d.user_id = wc.user_id 
                     AND d.peer_id = wc.telegram_chat_id) as last_digest_at
                FROM web_channels wc
                WHERE wc.user_id = %s
                ORDER BY wc.created_at DESC
            """, (user_id,))
            
            channels = cur.fetchall()
            
            return {
                "channels": [
                    {
                        "id": ch['id'],
                        "telegram_chat_id": ch['telegram_chat_id'],
                        "name": ch['name'],
                        "description": ch['description'],
                        "peer_type": ch['peer_type'],
                        "enabled": ch['enabled'],
                        "recipient_telegram_id": ch['recipient_telegram_id'],
                        "recipient_name": ch['recipient_name'],
                        "created_at": ch['created_at'].isoformat() if ch['created_at'] else None,
                        "last_digest_at": ch['last_digest_at'].isoformat() if ch['last_digest_at'] else None,
                        "total_messages": ch['total_messages'] or 0,
                        "access_method": ch.get('access_method', 'system_session'),
                        "access_status": ch.get('access_status', 'available'),
                        "consolidated_doc_path": ch.get('consolidated_doc_path'),
                    }
                    for ch in channels
                ]
            }
    except Exception as e:
        logger.error(f"Ошибка получения каналов: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels", response_class=JSONResponse)
async def create_channel(
    channel: ChannelCreate,
    user_telegram_id: int = Form(...),
    db=Depends(get_db)
):
    """Добавляет новый канал для пользователя"""
    try:
        # Получаем или создаём пользователя
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        # Проверяем доступ к чату (только через системную сессию)
        has_access, peer_type, chat_name = await check_chat_access(channel.telegram_chat_id)
        
        if not has_access:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Система не имеет доступа к чату {channel.telegram_chat_id}. "
                    "В настоящее время система находится в стадии тестирования и поддерживает работу "
                    "только с чатами, где она присутствует. Работа со сторонними ресурсами пока не поддерживается."
                )
            )
        
        # Если доступ есть - всё готово
        access_method = "system_session"
        access_status = "available"
        
        # Используем название из проверки или переданное
        final_name = channel.name or chat_name or f"Chat {channel.telegram_chat_id}"
        
        # Формируем путь к сводному документу
        doc_name = final_name.lower().replace(' ', '_').replace('/', '_')
        doc_name = ''.join(c for c in doc_name if c.isalnum() or c in '_-')
        consolidated_doc_path = f"docs/reference/{doc_name}.md"
        
        # Создаём запись в БД
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO web_channels (
                    user_id, telegram_chat_id, name, description, peer_type,
                    prompt_file, consolidated_doc_path, consolidated_doc_prompt_file,
                    poll_interval_minutes, enabled, recipient_telegram_id, recipient_name,
                    access_method, access_status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                user_id,
                channel.telegram_chat_id,
                final_name,
                f"Добавлен через веб-интерфейс",
                peer_type,
                channel.prompt_file,
                consolidated_doc_path,
                "prompts/consolidated_engineering.md",
                channel.poll_interval_minutes,
                True,
                channel.recipient_telegram_id,
                channel.recipient_name or f"User {channel.recipient_telegram_id}",
                access_method,
                access_status
            ))
            
            channel_id = cur.fetchone()['id']
            db.commit()
        
        # Запускаем фоновую задачу загрузки истории
        # В реальности это должно быть через Celery или подобное
        # Здесь просто возвращаем успех, загрузка будет при следующем цикле воркера
        
        message = f"Канал {final_name} добавлен. История будет загружена автоматически."
        if bot_required:
            message += f"\n\n⚠️ Внимание: Для работы канала необходимо добавить бота в чат. См. инструкции в настройках канала."
        
        return {
            "success": True,
            "channel_id": channel_id,
            "message": f"Канал {final_name} добавлен. История будет загружена автоматически.",
            "access_method": access_method,
            "access_status": access_status
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка добавления канала: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channels/{channel_id}", response_class=JSONResponse)
async def delete_channel(channel_id: int, user_telegram_id: int, db=Depends(get_db)):
    """Удаляет канал пользователя"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        with db.cursor() as cur:
            cur.execute("""
                DELETE FROM web_channels 
                WHERE id = %s AND user_id = %s
            """, (channel_id, user_id))
            
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Канал не найден")
            
            db.commit()
        
        return {"success": True, "message": "Канал удалён"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка удаления канала: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/digests/{channel_id}", response_class=JSONResponse)
async def get_digests(channel_id: int, user_telegram_id: int, limit: int = 10, db=Depends(get_db)):
    """Возвращает последние дайджесты канала"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            # Проверяем что канал принадлежит пользователю
            cur.execute("""
                SELECT telegram_chat_id FROM web_channels 
                WHERE id = %s AND user_id = %s
            """, (channel_id, user_id))
            
            channel = cur.fetchone()
            if not channel:
                raise HTTPException(status_code=404, detail="Канал не найден")
            
            # Получаем дайджесты
            cur.execute("""
                SELECT id, peer_id, msg_id_from, msg_id_to, digest_llm, created_at
                FROM rpt.digests
                WHERE user_id = %s AND peer_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (user_id, channel['telegram_chat_id'], limit))
            
            digests = cur.fetchall()
            
            return {
                "digests": [
                    {
                        "id": d['id'],
                        "msg_id_from": d['msg_id_from'],
                        "msg_id_to": d['msg_id_to'],
                        "digest_preview": (d['digest_llm'] or "")[:200] + "..." if d['digest_llm'] else None,
                        "created_at": d['created_at'].isoformat() if d['created_at'] else None,
                    }
                    for d in digests
                ]
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения дайджестов: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channels/{channel_id}/document", response_class=FileResponse)
async def get_consolidated_document(channel_id: int, user_telegram_id: int, db=Depends(get_db)):
    """Возвращает сводный инженерный документ канала"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            # Проверяем что канал принадлежит пользователю
            cur.execute("""
                SELECT consolidated_doc_path, telegram_chat_id 
                FROM web_channels 
                WHERE id = %s AND user_id = %s
            """, (channel_id, user_id))
            
            channel = cur.fetchone()
            if not channel:
                raise HTTPException(status_code=404, detail="Канал не найден")
            
            doc_path = channel.get('consolidated_doc_path')
            if not doc_path:
                raise HTTPException(status_code=404, detail="Сводный документ не создан")
            
            # Получаем путь к репозиторию из env
            repo_dir = Path(os.environ.get("REPO_DIR", "/app"))
            full_path = repo_dir / doc_path
            
            if not full_path.exists():
                raise HTTPException(status_code=404, detail="Файл документа не найден")
            
            return FileResponse(
                path=str(full_path),
                filename=full_path.name,
                media_type="text/markdown"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения документа: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channels/{channel_id}/prompts", response_class=JSONResponse)
async def get_channel_prompts(channel_id: int, user_telegram_id: int, prompt_type: Optional[str] = None, db=Depends(get_db)):
    """Возвращает промпты канала для редактирования"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            # Проверяем что канал принадлежит пользователю
            cur.execute("""
                SELECT id FROM web_channels 
                WHERE id = %s AND user_id = %s
            """, (channel_id, user_id))
            
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Канал не найден")
            
            # Загружаем промпты из таблицы channel_prompts
            if prompt_type:
                cur.execute("""
                    SELECT id, prompt_type, name, text, is_default, created_at, updated_at
                    FROM channel_prompts
                    WHERE channel_id = %s AND prompt_type = %s
                    ORDER BY is_default DESC, created_at DESC
                """, (channel_id, prompt_type))
            else:
                cur.execute("""
                    SELECT id, prompt_type, name, text, is_default, created_at, updated_at
                    FROM channel_prompts
                    WHERE channel_id = %s
                    ORDER BY prompt_type, is_default DESC, created_at DESC
                """, (channel_id,))
            
            prompts_list = cur.fetchall()
            
            # Если промптов нет, возвращаем дефолтные из web_channels
            if not prompts_list:
                cur.execute("""
                    SELECT prompt_file, prompt_text, consolidated_doc_prompt_file, consolidated_doc_prompt_text
                    FROM web_channels
                    WHERE id = %s
                """, (channel_id,))
                channel = cur.fetchone()
                
                if channel:
                    # Загружаем из файлов если нужно
                    prompt_text = channel.get('prompt_text')
                    consolidated_prompt_text = channel.get('consolidated_doc_prompt_text')
                    
                    if not prompt_text and channel.get('prompt_file'):
                        prompt_path = Path(os.environ.get("PROMPTS_DIR", "/app/prompts")) / Path(channel['prompt_file']).name
                        if prompt_path.exists():
                            try:
                                prompt_text = prompt_path.read_text(encoding="utf-8")
                            except Exception as e:
                                logger.warning(f"Не удалось загрузить промпт из файла {prompt_path}: {e}")
                    
                    if not consolidated_prompt_text and channel.get('consolidated_doc_prompt_file'):
                        cons_prompt_path = Path(os.environ.get("PROMPTS_DIR", "/app/prompts")) / Path(channel['consolidated_doc_prompt_file']).name
                        if cons_prompt_path.exists():
                            try:
                                consolidated_prompt_text = cons_prompt_path.read_text(encoding="utf-8")
                            except Exception as e:
                                logger.warning(f"Не удалось загрузить промпт сводного документа из файла {cons_prompt_path}: {e}")
                    
                    return {
                        "prompts": {
                            "digest": [{
                                "id": "default_digest",
                                "name": "Промпт для дайджестов",
                                "text": prompt_text or "",
                                "is_default": True
                            }],
                            "consolidated": [{
                                "id": "default_consolidated",
                                "name": "Промпт для сводного документа",
                                "text": consolidated_prompt_text or "",
                                "is_default": True
                            }]
                        }
                    }
            
            # Группируем промпты по типам
            prompts_by_type = {"digest": [], "consolidated": []}
            for p in prompts_list:
                prompts_by_type[p['prompt_type']].append({
                    "id": p['id'],
                    "name": p['name'],
                    "text": p['text'],
                    "is_default": p['is_default'],
                    "created_at": p['created_at'].isoformat() if p['created_at'] else None,
                    "updated_at": p['updated_at'].isoformat() if p['updated_at'] else None
                })
            
            return {"prompts": prompts_by_type}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения промптов: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels/{channel_id}/prompts", response_class=JSONResponse)
async def create_channel_prompt(
    channel_id: int,
    user_telegram_id: int = Form(...),
    prompt_type: str = Form(...),
    name: str = Form(...),
    text: str = Form(...),
    is_default: bool = Form(False),
    db=Depends(get_db)
):
    """Создаёт новый промпт для канала"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        if prompt_type not in ['digest', 'consolidated']:
            raise HTTPException(status_code=400, detail="prompt_type должен быть 'digest' или 'consolidated'")
        
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            # Проверяем что канал принадлежит пользователю
            cur.execute("""
                SELECT id FROM web_channels 
                WHERE id = %s AND user_id = %s
            """, (channel_id, user_id))
            
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Канал не найден")
            
            # Если это промпт по умолчанию, снимаем флаг с других
            if is_default:
                cur.execute("""
                    UPDATE channel_prompts 
                    SET is_default = false 
                    WHERE channel_id = %s AND prompt_type = %s
                """, (channel_id, prompt_type))
            
            # Создаём новый промпт
            cur.execute("""
                INSERT INTO channel_prompts (channel_id, user_id, prompt_type, name, text, is_default)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (channel_id, user_id, prompt_type, name, text, is_default))
            
            prompt_id = cur.fetchone()['id']
            db.commit()
        
        return {"success": True, "prompt_id": prompt_id, "message": "Промпт создан"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка создания промпта: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/channels/{channel_id}/prompts/{prompt_id}", response_class=JSONResponse)
async def update_channel_prompt(
    channel_id: int,
    prompt_id: int,
    user_telegram_id: int = Form(...),
    name: Optional[str] = Form(None),
    text: Optional[str] = Form(None),
    is_default: Optional[bool] = Form(None),
    db=Depends(get_db)
):
    """Обновляет промпт канала"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        with db.cursor() as cur:
            # Проверяем что промпт принадлежит каналу пользователя
            cur.execute("""
                SELECT cp.id, cp.prompt_type 
                FROM channel_prompts cp
                JOIN web_channels wc ON cp.channel_id = wc.id
                WHERE cp.id = %s AND cp.channel_id = %s AND wc.user_id = %s
            """, (prompt_id, channel_id, user_id))
            
            prompt = cur.fetchone()
            if not prompt:
                raise HTTPException(status_code=404, detail="Промпт не найден")
            
            # Если устанавливаем как default, снимаем флаг с других
            if is_default is True:
                cur.execute("""
                    UPDATE channel_prompts 
                    SET is_default = false 
                    WHERE channel_id = %s AND prompt_type = %s AND id != %s
                """, (channel_id, prompt[1], prompt_id))
            
            # Обновляем промпт
            update_fields = []
            params = []
            
            if name is not None:
                update_fields.append("name = %s")
                params.append(name)
            
            if text is not None:
                update_fields.append("text = %s")
                params.append(text)
            
            if is_default is not None:
                update_fields.append("is_default = %s")
                params.append(is_default)
            
            if update_fields:
                update_fields.append("updated_at = now()")
                params.extend([prompt_id, channel_id, user_id])
                
                cur.execute(f"""
                    UPDATE channel_prompts 
                    SET {', '.join(update_fields)}
                    WHERE id = %s AND channel_id = %s 
                    AND EXISTS (
                        SELECT 1 FROM web_channels wc 
                        WHERE wc.id = channel_prompts.channel_id AND wc.user_id = %s
                    )
                """, params)
                
                db.commit()
        
        return {"success": True, "message": "Промпт обновлён"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка обновления промпта: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channels/{channel_id}/prompts/{prompt_id}", response_class=JSONResponse)
async def delete_channel_prompt(
    channel_id: int,
    prompt_id: int,
    user_telegram_id: int,
    db=Depends(get_db)
):
    """Удаляет промпт канала"""
    try:
        user_id = get_or_create_user(db, user_telegram_id, None)
        
        with db.cursor() as cur:
            # Проверяем что промпт принадлежит каналу пользователя
            cur.execute("""
                DELETE FROM channel_prompts
                WHERE id = %s AND channel_id = %s 
                AND EXISTS (
                    SELECT 1 FROM web_channels wc 
                    WHERE wc.id = channel_prompts.channel_id AND wc.user_id = %s
                )
            """, (prompt_id, channel_id, user_id))
            
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Промпт не найден")
            
            db.commit()
        
        return {"success": True, "message": "Промпт удалён"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка удаления промпта: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health", response_class=JSONResponse)
async def health():
    """Healthcheck endpoint"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
