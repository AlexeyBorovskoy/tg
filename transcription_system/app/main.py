from __future__ import annotations

import difflib
import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .db import JobCreate, SqliteStore
from .formatting import (
    apply_glossary_to_result_with_stats,
    transcript_markdown_from_text,
    transcript_text_from_result,
)
from .llm_postprocess import run_llm_postprocess
from .protocol_builder import build_meeting_protocol, protocol_markdown
from .role_assignment import run_role_assignment
from .shared_auth import PostgresSharedAuth, SharedAuthError
from .settings import ensure_dirs, settings
from .transcribe import TranscriptionError, is_supported_audio, transcribe_audio


app = FastAPI(title="Transcription System", version="1.0.0")
store = SqliteStore(settings.sqlite_path)
shared_auth = PostgresSharedAuth()
logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
templates.env.globals["provider"] = settings.provider
SUPPORTED_PROVIDERS = ("assemblyai", "local_whisper", "compare", "mock")
AUTH_LOCAL_ENABLED = settings.auth_local_enabled or settings.auth_shared_enabled
AUTH_LOCAL_COOKIE_NAME = settings.auth_shared_cookie_name
AUTH_LOCAL_MIN_PASSWORD_LEN = 8


def _request_id() -> str:
    return uuid.uuid4().hex[:12]


def ok(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": True, "data": data, "error": None, "meta": {"request_id": _request_id()}},
    )


def err(message: str, code: str = "bad_request", status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "data": None,
            "error": {"code": code, "message": message},
            "meta": {"request_id": _request_id()},
        },
    )


def _normalize_login(login: str) -> str:
    return (login or "").strip().lower()


def _is_valid_login(login: str) -> bool:
    raw = _normalize_login(login)
    if len(raw) < 3 or len(raw) > 64:
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_.-")
    return all(ch in allowed for ch in raw)


def _is_valid_password(password: str) -> bool:
    return AUTH_LOCAL_MIN_PASSWORD_LEN <= len(password or "") <= 256


def _normalize_next_path(next_path: str | None) -> str:
    p = unquote((next_path or "/resources").strip())
    if not p.startswith("/") or p.startswith("//"):
        return "/resources"
    return p


def _post_login_redirect(next_path: str | None) -> str:
    p = _normalize_next_path(next_path)
    if p in {"/", "/login", "/register"}:
        return "/resources"
    return p


def _is_public_path(path: str) -> bool:
    if path in {"/login", "/register", "/logout", "/health"}:
        return True
    if path.startswith("/static/"):
        return True
    return False


def _is_api_path(path: str) -> bool:
    return path.startswith("/api/")


def _is_admin_user(user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    return str(user.get("role") or "user").lower() == "admin"


def _template_ctx(request: Request, **extra: Any) -> dict[str, Any]:
    current_user = getattr(request.state, "auth_user", None)
    base = {
        "request": request,
        "provider": settings.provider,
        "current_user": current_user,
        "is_admin": _is_admin_user(current_user),
        "auth_shared_enabled": settings.auth_shared_enabled,
        "auth_shared_login_url": settings.auth_shared_login_url,
        "auth_shared_register_url": settings.auth_shared_register_url,
    }
    base.update(extra)
    return base


def _load_user_from_session(token: str | None) -> dict[str, Any] | None:
    token_s = (token or "").strip()
    if not token_s:
        return None
    if settings.auth_shared_enabled:
        shared_user = shared_auth.get_user_by_session(token_s)
        if shared_user:
            return shared_user
    return store.get_user_by_session(token_s)


def _authenticate_user(login: str, password: str) -> dict[str, Any] | None:
    if settings.auth_shared_enabled:
        user = shared_auth.authenticate_local(login, password)
        if user:
            return user
    return store.authenticate_local(login, password)


def _create_user_session(user_id: int) -> str:
    if settings.auth_shared_enabled:
        return shared_auth.create_session(user_id, days=settings.auth_session_days)
    return store.create_session(user_id, days=settings.auth_session_days)


def _delete_user_session(token: str | None) -> None:
    token_s = (token or "").strip()
    if not token_s:
        return
    if settings.auth_shared_enabled:
        shared_auth.delete_session(token_s)
    store.delete_session(token_s)


@app.middleware("http")
async def require_auth_middleware(request: Request, call_next):
    if not AUTH_LOCAL_ENABLED:
        admin = store.get_user_by_login(settings.admin_login)
        request.state.auth_user = admin
        return await call_next(request)

    path = request.url.path.rstrip("/") or "/"
    token = request.cookies.get(AUTH_LOCAL_COOKIE_NAME)
    user = _load_user_from_session(token)
    request.state.auth_user = user

    if _is_public_path(path):
        return await call_next(request)

    if user:
        return await call_next(request)

    if _is_api_path(path):
        return err("Требуется авторизация", code="unauthorized", status_code=401)

    next_path = request.url.path
    if request.query_params:
        next_path = next_path + "?" + str(request.query_params)
    return RedirectResponse(url=f"/login?next={quote(next_path, safe='')}", status_code=302)


def _require_api_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "auth_user", None)
    if user:
        return user
    if not AUTH_LOCAL_ENABLED:
        admin = store.get_user_by_login(settings.admin_login)
        if admin:
            return admin
    raise HTTPException(status_code=401, detail="Требуется авторизация")


class SpeakerMapPayload(BaseModel):
    map: dict[str, str] = Field(default_factory=dict)


class GlossaryTermPayload(BaseModel):
    wrong: str
    correct: str


class PromptPayload(BaseModel):
    name: str
    profile: str = Field(default="meeting")
    system_prompt: str
    user_template: str
    is_default: bool = False
    is_active: bool = True


@app.on_event("startup")
def startup() -> None:
    ensure_dirs()
    store.init_db()
    if settings.auth_shared_enabled and not shared_auth.available:
        logger.warning("Shared auth requested but unavailable. Install psycopg2 and check PG* env.")


@app.get("/login")
def page_login(request: Request, next_url: str | None = None, error_msg: str | None = None):
    current_user = getattr(request.state, "auth_user", None)
    if current_user:
        return RedirectResponse(url="/resources", status_code=302)
    next_path = _normalize_next_path(next_url or request.query_params.get("next"))
    return templates.TemplateResponse(
        "login_local.html",
        _template_ctx(
            request,
            next_url=next_path,
            error_msg=error_msg or request.query_params.get("error"),
        ),
    )


@app.post("/login")
def page_login_submit(
    username: str = Form(default=""),
    password: str = Form(default=""),
    next_path: str = Form(default="/resources", alias="next"),
):
    if not AUTH_LOCAL_ENABLED:
        return RedirectResponse(url=_post_login_redirect(next_path), status_code=302)

    login = _normalize_login(username)
    if not login or not password:
        return RedirectResponse(
            url=f"/login?next={quote(_normalize_next_path(next_path), safe='')}&error={quote('Введите логин и пароль', safe='')}",
            status_code=302,
        )

    auth_user = _authenticate_user(login, password)
    if not auth_user:
        return RedirectResponse(
            url=f"/login?next={quote(_normalize_next_path(next_path), safe='')}&error={quote('Неверный логин или пароль', safe='')}",
            status_code=302,
        )

    try:
        session_token = _create_user_session(int(auth_user["id"]))
    except SharedAuthError:
        return RedirectResponse(
            url=f"/login?next={quote(_normalize_next_path(next_path), safe='')}&error={quote('Ошибка общей авторизации. Проверьте подключение к БД', safe='')}",
            status_code=302,
        )
    response = RedirectResponse(url=_post_login_redirect(next_path), status_code=302)
    max_age = max(1, int(settings.auth_session_days)) * 24 * 60 * 60
    response.set_cookie(
        key=AUTH_LOCAL_COOKIE_NAME,
        value=session_token,
        max_age=max_age,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/register")
def page_register(request: Request, next_url: str | None = None, error_msg: str | None = None):
    if not AUTH_LOCAL_ENABLED:
        return RedirectResponse(url="/login", status_code=302)
    if settings.auth_shared_enabled and settings.auth_shared_register_url:
        return RedirectResponse(
            url=f"{settings.auth_shared_register_url}?next={quote(_normalize_next_path(next_url), safe='')}",
            status_code=302,
        )
    current_user = getattr(request.state, "auth_user", None)
    if current_user:
        return RedirectResponse(url="/resources", status_code=302)
    next_path = _normalize_next_path(next_url or request.query_params.get("next"))
    return templates.TemplateResponse(
        "register_local.html",
        _template_ctx(
            request,
            next_url=next_path,
            error_msg=error_msg or request.query_params.get("error"),
            min_password_len=AUTH_LOCAL_MIN_PASSWORD_LEN,
        ),
    )


@app.post("/register")
def page_register_submit(
    login: str = Form(default=""),
    password: str = Form(default=""),
    password_confirm: str = Form(default=""),
    next_path: str = Form(default="/resources", alias="next"),
):
    if not AUTH_LOCAL_ENABLED:
        return RedirectResponse(url="/login", status_code=302)
    if settings.auth_shared_enabled and settings.auth_shared_register_url:
        return RedirectResponse(
            url=f"{settings.auth_shared_register_url}?next={quote(_normalize_next_path(next_path), safe='')}",
            status_code=302,
        )

    next_norm = _normalize_next_path(next_path)
    login_norm = _normalize_login(login)
    if not _is_valid_login(login_norm):
        return RedirectResponse(
            url=f"/register?next={quote(next_norm, safe='')}&error={quote('Логин: 3-64 символа [a-z0-9_.-]', safe='')}",
            status_code=302,
        )
    if not _is_valid_password(password):
        return RedirectResponse(
            url=f"/register?next={quote(next_norm, safe='')}&error={quote(f'Пароль минимум {AUTH_LOCAL_MIN_PASSWORD_LEN} символов', safe='')}",
            status_code=302,
        )
    if password != password_confirm:
        return RedirectResponse(
            url=f"/register?next={quote(next_norm, safe='')}&error={quote('Пароли не совпадают', safe='')}",
            status_code=302,
        )
    try:
        if settings.auth_shared_enabled:
            user = shared_auth.create_user(login_norm, password)
        else:
            user = store.create_user(login_norm, password, role="user", is_active=True)
    except (ValueError, SharedAuthError) as exc:
        return RedirectResponse(
            url=f"/register?next={quote(next_norm, safe='')}&error={quote(str(exc), safe='')}",
            status_code=302,
        )

    try:
        session_token = _create_user_session(int(user["id"]))
    except SharedAuthError:
        return RedirectResponse(
            url=f"/register?next={quote(next_norm, safe='')}&error={quote('Ошибка общей авторизации. Проверьте подключение к БД', safe='')}",
            status_code=302,
        )
    response = RedirectResponse(url=_post_login_redirect(next_norm), status_code=302)
    max_age = max(1, int(settings.auth_session_days)) * 24 * 60 * 60
    response.set_cookie(
        key=AUTH_LOCAL_COOKIE_NAME,
        value=session_token,
        max_age=max_age,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/logout")
def page_logout(request: Request):
    token = request.cookies.get(AUTH_LOCAL_COOKIE_NAME)
    if token:
        _delete_user_session(token)
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(AUTH_LOCAL_COOKIE_NAME, path="/")
    return response


@app.get("/resources")
def page_resources(request: Request):
    user = getattr(request.state, "auth_user", None)
    return templates.TemplateResponse(
        "resources.html",
        _template_ctx(
            request,
            tg_digest_url=settings.resource_tg_digest_url,
            resource_items=[
                {
                    "title": "Транскрибация аудио",
                    "description": "Загрузка аудио, расшифровка, роли, протокол и артефакты.",
                    "url": "/",
                    "tag": "transcription",
                },
                {
                    "title": "Telegram Digest",
                    "description": "Управление Telegram-каналами и рассылкой дайджестов.",
                    "url": settings.resource_tg_digest_url,
                    "tag": "digest",
                },
            ],
            login=(user or {}).get("login"),
        ),
    )


@app.get("/users")
def page_users(request: Request):
    user = getattr(request.state, "auth_user", None)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Admin only")
    users = shared_auth.list_users() if settings.auth_shared_enabled else store.list_users()
    return templates.TemplateResponse("users.html", _template_ctx(request, users=users))


@app.get("/api/v1/admin/users")
def api_admin_users(request: Request):
    user = _require_api_user(request)
    if not _is_admin_user(user):
        return err("Admin only", code="forbidden", status_code=403)
    users = shared_auth.list_users() if settings.auth_shared_enabled else store.list_users()
    return ok({"items": users})


@app.get("/")
def page_index(request: Request):
    user = getattr(request.state, "auth_user", None)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    jobs = store.list_jobs(limit=12, user_id=int(user["id"]), is_admin=_is_admin_user(user))
    prompts = store.list_prompts(active_only=True)
    return templates.TemplateResponse(
        "index.html",
        _template_ctx(
            request,
            jobs=jobs,
            prompts=prompts,
            providers=SUPPORTED_PROVIDERS,
            default_provider=settings.provider if settings.provider in SUPPORTED_PROVIDERS else "assemblyai",
        ),
    )


@app.get("/jobs")
def page_jobs(request: Request):
    user = getattr(request.state, "auth_user", None)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    jobs = store.list_jobs(limit=200, user_id=int(user["id"]), is_admin=_is_admin_user(user))
    return templates.TemplateResponse(
        "jobs.html",
        _template_ctx(request, jobs=jobs),
    )


@app.get("/glossary")
def page_glossary(request: Request):
    user = getattr(request.state, "auth_user", None)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    terms = store.list_glossary_terms()
    return templates.TemplateResponse(
        "glossary.html",
        _template_ctx(request, terms=terms),
    )


@app.get("/prompts")
def page_prompts(request: Request):
    user = getattr(request.state, "auth_user", None)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    prompts = store.list_prompts()
    return templates.TemplateResponse(
        "prompts.html",
        _template_ctx(request, prompts=prompts),
    )


@app.get("/instructions")
def page_instructions(request: Request):
    user = getattr(request.state, "auth_user", None)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("instructions.html", _template_ctx(request))


@app.post("/api/v1/transcription/jobs")
async def create_job(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    provider: str | None = Form(None),
    diarization: bool = Form(True),
    speakers_expected: int | None = Form(None),
    source_kind: str = Form("voice_message"),
    llm_enabled: bool = Form(settings.llm_enabled_default),
    llm_profile: str | None = Form(None),
    llm_model_override: str | None = Form(None),
    prompt_id: int | None = Form(None),
    roles_enabled: bool = Form(True),
):
    current_user = _require_api_user(request)
    if not file.filename:
        return err("Filename is empty", code="validation_error", status_code=422)
    if not is_supported_audio(file.filename):
        return err("Unsupported file format", code="validation_error", status_code=422)

    if source_kind not in {"voice_message", "meeting"}:
        return err("source_kind must be voice_message or meeting", code="validation_error", status_code=422)

    provider_name = (provider or settings.provider or "").strip().lower()
    if provider_name not in SUPPORTED_PROVIDERS:
        return err(
            "provider must be one of: assemblyai, local_whisper, compare, mock",
            code="validation_error",
            status_code=422,
        )

    selected_prompt = None
    if prompt_id:
        selected_prompt = store.get_prompt(prompt_id)
        if not selected_prompt:
            return err("prompt_id not found", code="validation_error", status_code=422)
        if not selected_prompt.get("is_active"):
            return err("prompt is inactive", code="validation_error", status_code=422)

    job_id = uuid.uuid4().hex
    safe_name = f"{job_id}_{Path(file.filename).name}"
    upload_path = settings.uploads_dir / safe_name

    payload = await file.read()
    upload_path.write_bytes(payload)

    profile = (llm_profile or "").strip().lower()
    if profile not in {"", "auto", "voice", "meeting"}:
        return err("llm_profile must be one of: auto, voice, meeting", code="validation_error", status_code=422)
    if not profile or profile == "auto":
        profile = "voice" if source_kind == "voice_message" else "meeting"

    store.create_job(
        JobCreate(
            id=job_id,
            user_id=int(current_user["id"]),
            input_filename=file.filename,
            upload_path=str(upload_path),
            provider=provider_name,
            diarization=diarization,
            speakers_expected=speakers_expected,
            source_kind=source_kind,
            llm_enabled=llm_enabled,
            llm_profile=profile,
            llm_model_override=(llm_model_override or "").strip() or None,
            prompt_id=prompt_id,
            roles_enabled=roles_enabled,
        )
    )

    background_tasks.add_task(run_transcription_job, job_id)

    return ok(
        {
            "job_id": job_id,
            "status": "queued",
            "input_filename": file.filename,
            "provider": provider_name,
            "source_kind": source_kind,
            "diarization": diarization,
            "speakers_expected": speakers_expected,
            "llm_enabled": llm_enabled,
            "llm_profile": profile,
            "llm_model_override": (llm_model_override or "").strip() or None,
            "prompt_id": prompt_id,
            "prompt_name": selected_prompt.get("name") if selected_prompt else None,
            "roles_enabled": roles_enabled,
        },
        status_code=201,
    )


@app.get("/api/v1/transcription/jobs")
def list_jobs(request: Request, limit: int = 50):
    current_user = _require_api_user(request)
    limit = max(1, min(limit, 500))
    rows = store.list_jobs(limit=limit, user_id=int(current_user["id"]), is_admin=_is_admin_user(current_user))
    return ok({"items": rows, "count": len(rows)})


@app.get("/health")
def health():
    return {"ok": True, "service": "transcription_system"}


@app.get("/api/v1/transcription/jobs/{job_id}")
def get_job(request: Request, job_id: str):
    current_user = _require_api_user(request)
    row = store.get_job(job_id, user_id=int(current_user["id"]), is_admin=_is_admin_user(current_user))
    if not row:
        return err("Job not found", code="not_found", status_code=404)
    return ok(row)


@app.get("/api/v1/transcription/jobs/{job_id}/segments")
def get_job_segments(request: Request, job_id: str):
    current_user = _require_api_user(request)
    row = store.get_job(job_id, user_id=int(current_user["id"]), is_admin=_is_admin_user(current_user))
    if not row:
        return err("Job not found", code="not_found", status_code=404)
    items = store.list_job_segments(job_id)
    return ok({"items": items, "count": len(items)})


@app.get("/api/v1/transcription/jobs/{job_id}/glossary-stats")
def get_job_glossary_stats(request: Request, job_id: str):
    current_user = _require_api_user(request)
    row = store.get_job(job_id, user_id=int(current_user["id"]), is_admin=_is_admin_user(current_user))
    if not row:
        return err("Job not found", code="not_found", status_code=404)
    items = store.list_job_glossary_stats(job_id)
    return ok({"items": items, "count": len(items)})


@app.get("/api/v1/transcription/jobs/{job_id}/protocol")
def get_job_protocol(request: Request, job_id: str):
    current_user = _require_api_user(request)
    row = store.get_job(job_id, user_id=int(current_user["id"]), is_admin=_is_admin_user(current_user))
    if not row:
        return err("Job not found", code="not_found", status_code=404)
    protocol = store.get_job_protocol(job_id)
    if not protocol:
        return err("Protocol not found", code="not_found", status_code=404)
    return ok(protocol)


@app.post("/api/v1/transcription/jobs/{job_id}/protocol/rebuild")
def rebuild_job_protocol(request: Request, job_id: str):
    current_user = _require_api_user(request)
    row = store.get_job(job_id, user_id=int(current_user["id"]), is_admin=_is_admin_user(current_user))
    if not row:
        return err("Job not found", code="not_found", status_code=404)
    if row.get("status") != "done":
        return err("Job is not completed", code="job_not_ready", status_code=409)

    raw = row.get("raw_result_json") or {}
    speaker_map = row.get("speaker_map_json") or {}
    if raw:
        transcript_text = transcript_text_from_result(raw, speaker_map)
    else:
        transcript_text = str(row.get("transcript_text") or "").strip()
    if not transcript_text:
        return err("Transcript text is empty", code="validation_error", status_code=422)

    protocol_json = _build_and_store_protocol_for_job(job_id, row, transcript_text)
    if not protocol_json:
        return err("Protocol is available only for source_kind=meeting", code="validation_error", status_code=422)
    return ok({"job_id": job_id, "protocol": protocol_json})


@app.get("/api/v1/transcription/jobs/{job_id}/artifacts")
def get_artifact(request: Request, job_id: str, format: str = "md"):
    current_user = _require_api_user(request)
    row = store.get_job(job_id, user_id=int(current_user["id"]), is_admin=_is_admin_user(current_user))
    if not row:
        return err("Job not found", code="not_found", status_code=404)
    if row.get("status") != "done":
        return err("Job is not completed", code="job_not_ready", status_code=409)

    fmt = format.lower()
    path_map = {
        "md": settings.results_dir / f"{job_id}.md",
        "txt": settings.results_dir / f"{job_id}.txt",
        "json": settings.results_dir / f"{job_id}.json",
        "protocol_md": settings.results_dir / f"{job_id}.protocol.md",
        "protocol_json": settings.results_dir / f"{job_id}.protocol.json",
        "compare_md": settings.results_dir / f"{job_id}.compare.md",
        "compare_json": settings.results_dir / f"{job_id}.compare.json",
        "assemblyai_md": settings.results_dir / f"{job_id}.assemblyai.md",
        "assemblyai_txt": settings.results_dir / f"{job_id}.assemblyai.txt",
        "assemblyai_json": settings.results_dir / f"{job_id}.assemblyai.json",
        "local_whisper_md": settings.results_dir / f"{job_id}.local_whisper.md",
        "local_whisper_txt": settings.results_dir / f"{job_id}.local_whisper.txt",
        "local_whisper_json": settings.results_dir / f"{job_id}.local_whisper.json",
    }
    if fmt not in path_map:
        return err("Unsupported artifact format", code="validation_error", status_code=422)

    artifact = path_map[fmt]
    if not artifact.exists():
        return err("Artifact file not found", code="not_found", status_code=404)

    if fmt.endswith("_md") or fmt == "md":
        media_type = "text/markdown"
        ext = "md"
    elif fmt.endswith("_txt") or fmt == "txt":
        media_type = "text/plain"
        ext = "txt"
    else:
        media_type = "application/json"
        ext = "json"
    return FileResponse(
        artifact,
        media_type=media_type,
        filename=f"transcript_{job_id[:8]}_{fmt}.{ext}",
    )


@app.put("/api/v1/transcription/jobs/{job_id}/speaker-map")
def update_speaker_map(request: Request, job_id: str, payload: SpeakerMapPayload):
    current_user = _require_api_user(request)
    row = store.get_job(job_id, user_id=int(current_user["id"]), is_admin=_is_admin_user(current_user))
    if not row:
        return err("Job not found", code="not_found", status_code=404)

    store.set_speaker_map(job_id, payload.map, source="user")

    raw = row.get("raw_result_json") or {}
    if raw:
        transcript_text = transcript_text_from_result(raw, payload.map)
        transcript_md = transcript_markdown_from_text(
            transcript_text=transcript_text,
            source_filename=row.get("input_filename", "audio"),
            llm_status=row.get("llm_status"),
            llm_model_used=row.get("llm_model_used"),
        )
        store.update_job_transcript(job_id, transcript_text, transcript_md)

        (settings.results_dir / f"{job_id}.txt").write_text(transcript_text, encoding="utf-8")
        (settings.results_dir / f"{job_id}.md").write_text(transcript_md, encoding="utf-8")
        _build_and_store_protocol_for_job(job_id, row, transcript_text)

    return ok({"job_id": job_id, "speaker_map": payload.map})


@app.get("/api/v1/glossary")
def get_glossary(request: Request):
    _require_api_user(request)
    terms = store.list_glossary_terms()
    return ok({"items": terms, "count": len(terms)})


@app.post("/api/v1/glossary/terms")
def add_glossary_term(request: Request, payload: GlossaryTermPayload):
    _require_api_user(request)
    wrong = payload.wrong.strip()
    correct = payload.correct.strip()
    if not wrong or not correct:
        return err("Both wrong and correct are required", code="validation_error", status_code=422)
    row = store.add_glossary_term(wrong, correct)
    return ok(row, status_code=201)


@app.delete("/api/v1/glossary/terms/{term_id}")
def remove_glossary_term(request: Request, term_id: int):
    _require_api_user(request)
    deleted = store.delete_glossary_term(term_id)
    if not deleted:
        return err("Glossary term not found", code="not_found", status_code=404)
    return ok({"deleted": True, "term_id": term_id})


@app.get("/api/v1/prompts")
def list_prompts(request: Request, profile: str | None = None, active_only: bool = False):
    _require_api_user(request)
    if profile and profile not in {"voice", "meeting"}:
        return err("profile must be voice or meeting", code="validation_error", status_code=422)
    rows = store.list_prompts(profile=profile, active_only=active_only)
    return ok({"items": rows, "count": len(rows)})


@app.post("/api/v1/prompts")
def create_prompt(request: Request, payload: PromptPayload):
    _require_api_user(request)
    profile = payload.profile.strip().lower()
    if profile not in {"voice", "meeting"}:
        return err("profile must be voice or meeting", code="validation_error", status_code=422)
    try:
        row = store.create_prompt(
            name=payload.name,
            profile=profile,
            system_prompt=payload.system_prompt,
            user_template=payload.user_template,
            is_default=payload.is_default,
            is_active=payload.is_active,
        )
    except sqlite3.IntegrityError:
        return err("Prompt name must be unique", code="conflict", status_code=409)
    return ok(row, status_code=201)


@app.put("/api/v1/prompts/{prompt_id}")
def update_prompt(request: Request, prompt_id: int, payload: PromptPayload):
    _require_api_user(request)
    profile = payload.profile.strip().lower()
    if profile not in {"voice", "meeting"}:
        return err("profile must be voice or meeting", code="validation_error", status_code=422)
    try:
        row = store.update_prompt(
            prompt_id=prompt_id,
            name=payload.name,
            profile=profile,
            system_prompt=payload.system_prompt,
            user_template=payload.user_template,
            is_default=payload.is_default,
            is_active=payload.is_active,
        )
    except sqlite3.IntegrityError:
        return err("Prompt name must be unique", code="conflict", status_code=409)
    if not row:
        return err("Prompt not found", code="not_found", status_code=404)
    return ok(row)


@app.delete("/api/v1/prompts/{prompt_id}")
def delete_prompt(request: Request, prompt_id: int):
    _require_api_user(request)
    deleted = store.delete_prompt(prompt_id)
    if not deleted:
        return err("Prompt not found", code="not_found", status_code=404)
    return ok({"deleted": True, "prompt_id": prompt_id})


@app.post("/api/v1/prompts/{prompt_id}/activate")
def activate_prompt(request: Request, prompt_id: int):
    _require_api_user(request)
    row = store.activate_prompt(prompt_id)
    if not row:
        return err("Prompt not found", code="not_found", status_code=404)
    return ok(row)


@app.get("/api/v1/prompts/export")
def export_prompts(request: Request, active_only: bool = False):
    _require_api_user(request)
    package = store.export_prompts_package(active_only=active_only)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    suffix = "_active" if active_only else "_all"
    filename = f"prompt_library_export{suffix}_{stamp}.json"
    body = json.dumps(package, ensure_ascii=False, indent=2)
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/v1/prompts/import")
async def import_prompts(request: Request, file: UploadFile = File(...), mode: str = Form("merge")):
    _require_api_user(request)
    if mode not in {"merge", "replace"}:
        return err("mode must be merge or replace", code="validation_error", status_code=422)

    raw = await file.read()
    if not raw:
        return err("empty import file", code="validation_error", status_code=422)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return err("invalid JSON payload", code="validation_error", status_code=422)

    try:
        stats = store.import_prompts(payload, mode=mode)
    except ValueError as exc:
        return err(str(exc), code="validation_error", status_code=422)

    return ok(stats)


def _resolve_prompt(row: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    prompt_template = None
    prompt_name_used = None
    if row.get("prompt_id"):
        prompt_template = store.get_prompt(int(row["prompt_id"]))
    if not prompt_template:
        prompt_template = store.get_default_prompt((row.get("llm_profile") or "meeting"))
    if prompt_template:
        prompt_name_used = prompt_template.get("name")
    return prompt_template, prompt_name_used


async def _postprocess_transcript_result(
    row: dict[str, Any],
    normalized: dict[str, Any],
    prompt_template: dict[str, Any] | None,
    prompt_name_used: str | None,
) -> dict[str, Any]:
    speaker_map = row.get("speaker_map_json") or {}
    roles_result = await run_role_assignment(
        result=normalized,
        source_kind=row.get("source_kind") or "meeting",
        enabled=bool(row.get("roles_enabled", True)),
        existing_map=speaker_map,
    )
    if roles_result.speaker_map:
        speaker_map = roles_result.speaker_map
    base_text = transcript_text_from_result(normalized, speaker_map)

    llm_result = await run_llm_postprocess(
        transcript_text=base_text,
        source_kind=row.get("source_kind") or "meeting",
        llm_profile=row.get("llm_profile"),
        model_override=row.get("llm_model_override"),
        enabled=bool(row.get("llm_enabled", settings.llm_enabled_default)),
        prompt_template=prompt_template,
    )
    transcript_text = llm_result.text
    transcript_md = transcript_markdown_from_text(
        transcript_text=transcript_text,
        source_filename=row["input_filename"],
        llm_status=llm_result.status,
        llm_model_used=llm_result.model_used,
    )
    return {
        "normalized": normalized,
        "speaker_map": speaker_map,
        "roles_result": roles_result,
        "llm_result": llm_result,
        "base_text": base_text,
        "transcript_text": transcript_text,
        "transcript_md": transcript_md,
        "prompt_name_used": prompt_name_used,
    }


def _write_artifacts(job_id: str, transcript_text: str, transcript_md: str, normalized: dict[str, Any]) -> None:
    (settings.results_dir / f"{job_id}.txt").write_text(transcript_text, encoding="utf-8")
    (settings.results_dir / f"{job_id}.md").write_text(transcript_md, encoding="utf-8")
    (settings.results_dir / f"{job_id}.json").write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_provider_artifacts(
    job_id: str,
    provider_name: str,
    transcript_text: str,
    transcript_md: str,
    normalized: dict[str, Any],
) -> None:
    suffix = f".{provider_name}"
    (settings.results_dir / f"{job_id}{suffix}.txt").write_text(transcript_text, encoding="utf-8")
    (settings.results_dir / f"{job_id}{suffix}.md").write_text(transcript_md, encoding="utf-8")
    (settings.results_dir / f"{job_id}{suffix}.json").write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_protocol_artifacts(job_id: str, protocol_md: str, protocol_json: dict[str, Any]) -> None:
    (settings.results_dir / f"{job_id}.protocol.md").write_text(protocol_md, encoding="utf-8")
    (settings.results_dir / f"{job_id}.protocol.json").write_text(
        json.dumps(protocol_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_and_store_protocol_for_job(
    job_id: str,
    row: dict[str, Any],
    transcript_text: str,
) -> dict[str, Any] | None:
    source_kind = str(row.get("source_kind") or "").strip().lower()
    if source_kind != "meeting":
        return None
    protocol_json = build_meeting_protocol(
        transcript_text=transcript_text,
        source_filename=row.get("input_filename", "audio"),
    )
    protocol_md = protocol_markdown(protocol_json)
    store.set_job_protocol(job_id, protocol_md, protocol_json, status="generated")
    _write_protocol_artifacts(job_id, protocol_md, protocol_json)
    return protocol_json


def _cleanup_uploaded_audio(upload_path: Path | None) -> None:
    if settings.keep_uploaded_audio:
        return
    if not upload_path:
        return
    try:
        if upload_path.exists():
            upload_path.unlink()
    except Exception as exc:
        logger.warning("Failed to delete uploaded audio %s: %s", upload_path, exc)


def _build_compare_report(
    job_id: str,
    input_filename: str,
    runs: dict[str, dict[str, Any]],
    errors: dict[str, str],
    primary_provider: str,
) -> tuple[str, dict[str, Any]]:
    providers = ["assemblyai", "local_whisper"]
    texts = {p: (runs[p]["transcript_text"] if p in runs else "") for p in providers}
    a_text = texts.get("assemblyai", "")
    w_text = texts.get("local_whisper", "")
    similarity = difflib.SequenceMatcher(a=a_text, b=w_text).ratio() if a_text and w_text else 0.0

    summary: dict[str, Any] = {
        "job_id": job_id,
        "input_filename": input_filename,
        "primary_provider": primary_provider,
        "similarity_ratio": round(similarity, 4),
        "providers": {},
        "errors": errors,
    }

    lines: list[str] = []
    lines.append("# Сравнение ASR провайдеров")
    lines.append("")
    lines.append(f"- Файл: `{input_filename}`")
    lines.append(f"- Primary: `{primary_provider}`")
    lines.append(f"- Similarity (assemblyai vs local_whisper): `{similarity:.4f}`")
    if errors:
        lines.append(f"- Ошибки: `{'; '.join(f'{k}: {v}' for k, v in errors.items())}`")
    lines.append("")
    lines.append("## Метрики")
    lines.append("")
    lines.append("| Provider | status | chars | lines | llm_status | roles_status |")
    lines.append("|---|---:|---:|---:|---|---|")

    for provider_name in providers:
        if provider_name in runs:
            rec = runs[provider_name]
            text = rec["transcript_text"]
            llm_res = rec["llm_result"]
            roles_res = rec["roles_result"]
            line_count = len([ln for ln in text.splitlines() if ln.strip()])
            lines.append(
                f"| {provider_name} | ok | {len(text)} | {line_count} | "
                f"{llm_res.status} | {roles_res.status} |"
            )
            summary["providers"][provider_name] = {
                "status": "ok",
                "chars": len(text),
                "lines": line_count,
                "llm_status": llm_res.status,
                "roles_status": roles_res.status,
            }
        else:
            msg = errors.get(provider_name, "failed")
            lines.append(f"| {provider_name} | error | 0 | 0 | - | - |")
            summary["providers"][provider_name] = {"status": "error", "error": msg}

    for provider_name in providers:
        lines.append("")
        lines.append(f"## Текст: {provider_name}")
        lines.append("")
        if provider_name in runs:
            lines.append(runs[provider_name]["transcript_text"].strip() or "_Пусто_")
        else:
            lines.append(f"_Ошибка: {errors.get(provider_name, 'failed')}_")

    return "\n".join(lines).strip() + "\n", summary


async def run_transcription_job(job_id: str) -> None:
    row = store.get_job(job_id)
    if not row:
        return

    upload_path: Path | None = None
    try:
        store.set_job_running(job_id)
        glossary = store.glossary_map()
        boost_words = sorted({v.strip() for v in glossary.values() if str(v).strip()})
        prompt_template, prompt_name_used = _resolve_prompt(row)
        provider_mode = str(row.get("provider") or settings.provider).strip().lower()
        upload_path = Path(row["upload_path"])

        if provider_mode == "compare":
            runs: dict[str, dict[str, Any]] = {}
            errors: dict[str, str] = {}

            for provider_name in ("assemblyai", "local_whisper"):
                try:
                    raw = await transcribe_audio(
                        file_path=upload_path,
                        diarization=bool(row["diarization"]),
                        speakers_expected=row.get("speakers_expected"),
                        language_code="ru",
                        boost_words=boost_words,
                        provider=provider_name,
                    )
                    normalized, glossary_stats = apply_glossary_to_result_with_stats(raw, glossary)
                    rec = await _postprocess_transcript_result(row, normalized, prompt_template, prompt_name_used)
                    rec["glossary_stats"] = glossary_stats
                    runs[provider_name] = rec
                    _write_provider_artifacts(
                        job_id,
                        provider_name,
                        rec["transcript_text"],
                        rec["transcript_md"],
                        rec["normalized"],
                    )
                except Exception as exc:
                    errors[provider_name] = str(exc)

            if not runs:
                raise TranscriptionError("compare failed for both providers")

            primary_provider = "assemblyai" if "assemblyai" in runs else next(iter(runs.keys()))
            primary = runs[primary_provider]
            _write_artifacts(
                job_id,
                primary["transcript_text"],
                primary["transcript_md"],
                primary["normalized"],
            )
            store.replace_job_segments(job_id, primary["normalized"].get("utterances") or [])
            store.replace_job_glossary_stats(job_id, primary.get("glossary_stats") or [])
            _build_and_store_protocol_for_job(job_id, row, primary.get("base_text") or primary["transcript_text"])
            compare_md, compare_json = _build_compare_report(
                job_id=job_id,
                input_filename=row["input_filename"],
                runs=runs,
                errors=errors,
                primary_provider=primary_provider,
            )
            (settings.results_dir / f"{job_id}.compare.md").write_text(compare_md, encoding="utf-8")
            (settings.results_dir / f"{job_id}.compare.json").write_text(
                json.dumps(compare_json, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            existing_map = row.get("speaker_map_json") or {}
            if primary["speaker_map"] and primary["speaker_map"] != existing_map:
                store.set_speaker_map(job_id, primary["speaker_map"], source=primary["roles_result"].source)

            llm_error = primary["llm_result"].error
            if errors:
                extra = "compare_errors: " + "; ".join(f"{k}={v}" for k, v in errors.items())
                llm_error = f"{llm_error}; {extra}" if llm_error else extra

            store.set_job_done(
                job_id,
                primary["transcript_text"],
                primary["transcript_md"],
                primary["normalized"],
                llm_status=primary["llm_result"].status,
                llm_model_used=primary["llm_result"].model_used,
                llm_error=llm_error,
                llm_duration_ms=primary["llm_result"].duration_ms,
                llm_output_text=(
                    primary["llm_result"].text if primary["llm_result"].status.startswith("applied") else None
                ),
                prompt_name_used=primary["prompt_name_used"],
                roles_status=primary["roles_result"].status,
                roles_model_used=primary["roles_result"].model_used,
                roles_error=primary["roles_result"].error,
                roles_duration_ms=primary["roles_result"].duration_ms,
                speaker_map_source=primary["roles_result"].source if primary["speaker_map"] else None,
            )
            return

        raw = await transcribe_audio(
            file_path=upload_path,
            diarization=bool(row["diarization"]),
            speakers_expected=row.get("speakers_expected"),
            language_code="ru",
            boost_words=boost_words,
            provider=provider_mode,
        )
        normalized, glossary_stats = apply_glossary_to_result_with_stats(raw, glossary)
        rec = await _postprocess_transcript_result(row, normalized, prompt_template, prompt_name_used)
        _write_artifacts(job_id, rec["transcript_text"], rec["transcript_md"], rec["normalized"])
        store.replace_job_segments(job_id, rec["normalized"].get("utterances") or [])
        store.replace_job_glossary_stats(job_id, glossary_stats)
        _build_and_store_protocol_for_job(job_id, row, rec.get("base_text") or rec["transcript_text"])
        if provider_mode in {"assemblyai", "local_whisper"}:
            _write_provider_artifacts(
                job_id,
                provider_mode,
                rec["transcript_text"],
                rec["transcript_md"],
                rec["normalized"],
            )

        existing_map = row.get("speaker_map_json") or {}
        if rec["speaker_map"] and rec["speaker_map"] != existing_map:
            store.set_speaker_map(job_id, rec["speaker_map"], source=rec["roles_result"].source)

        store.set_job_done(
            job_id,
            rec["transcript_text"],
            rec["transcript_md"],
            rec["normalized"],
            llm_status=rec["llm_result"].status,
            llm_model_used=rec["llm_result"].model_used,
            llm_error=rec["llm_result"].error,
            llm_duration_ms=rec["llm_result"].duration_ms,
            llm_output_text=rec["llm_result"].text if rec["llm_result"].status.startswith("applied") else None,
            prompt_name_used=rec["prompt_name_used"],
            roles_status=rec["roles_result"].status,
            roles_model_used=rec["roles_result"].model_used,
            roles_error=rec["roles_result"].error,
            roles_duration_ms=rec["roles_result"].duration_ms,
            speaker_map_source=rec["roles_result"].source if rec["speaker_map"] else None,
        )
    except (TranscriptionError, HTTPException, RuntimeError, ValueError) as exc:
        store.set_job_error(job_id, str(exc))
    except Exception as exc:
        store.set_job_error(job_id, f"Unexpected error: {exc}")
    finally:
        _cleanup_uploaded_audio(upload_path)


app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parents[1] / "static")), name="static")
