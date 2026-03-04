from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


BASE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BASE_DIR.parent


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    raw = dotenv_values(path)
    return {k: str(v).strip() for k, v in raw.items() if k and v is not None and str(v).strip()}


LOCAL_ENV = _read_dotenv(BASE_DIR / ".env")
TG_SECRETS_ENV = _read_dotenv(PROJECT_ROOT / "tg_digest_system" / "docker" / "secrets.env")
TG_ENV = _read_dotenv(PROJECT_ROOT / "tg_digest_system" / "docker" / ".env")


def _env_get(*keys: str, default: str = "") -> str:
    for source in (os.environ, LOCAL_ENV, TG_SECRETS_ENV, TG_ENV):
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            value_s = str(value).strip()
            if value_s:
                return value_s
    return default


def _env_get_shared_first(*keys: str, default: str = "") -> str:
    for source in (os.environ, TG_SECRETS_ENV, TG_ENV, LOCAL_ENV):
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            value_s = str(value).strip()
            if value_s:
                return value_s
    return default


def _as_int(value: str, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _as_float(value: str, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _as_bool(value: str, default: bool) -> bool:
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    app_host: str = _env_get("APP_HOST", default="127.0.0.1")
    app_port: int = _as_int(_env_get("APP_PORT", default="8081"), 8081)
    app_debug: bool = _as_bool(_env_get("APP_DEBUG", default="0"), False)

    # Storage
    data_dir: Path = BASE_DIR / "data"
    uploads_dir: Path = BASE_DIR / "uploads"
    results_dir: Path = BASE_DIR / "results"
    sqlite_path: Path = BASE_DIR / "data" / "transcription.db"

    # Transcription provider: assemblyai | local_whisper | compare | mock
    provider: str = _env_get_shared_first("TRANSCRIPTION_PROVIDER", default="assemblyai").lower()
    assemblyai_api_key: str = _env_get_shared_first("ASSEMBLYAI_API_KEY", default="")
    assemblyai_speech_models_raw: str = _env_get_shared_first(
        "ASSEMBLYAI_SPEECH_MODELS",
        default="universal-2",
    )
    whisper_model_size: str = _env_get_shared_first("WHISPER_MODEL_SIZE", default="large-v3")
    whisper_device: str = _env_get_shared_first("WHISPER_DEVICE", default="auto")
    whisper_compute_type: str = _env_get_shared_first("WHISPER_COMPUTE_TYPE", default="int8")
    whisper_beam_size: int = _as_int(_env_get_shared_first("WHISPER_BEAM_SIZE", default="5"), 5)
    whisper_cpu_threads: int = _as_int(_env_get_shared_first("WHISPER_CPU_THREADS", default="4"), 4)
    whisper_vad_filter: bool = _as_bool(_env_get_shared_first("WHISPER_VAD_FILTER", default="1"), True)

    # LLM postprocessing by OpenAI-compatible API
    openai_api_key: str = _env_get_shared_first("OPENAI_API_KEY", default="")
    openai_base_url: str = _env_get_shared_first(
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        default="https://api.openai.com/v1",
    )
    openai_model: str = _env_get_shared_first("OPENAI_MODEL", default="gpt-4o-mini")
    openai_model_candidates_raw: str = _env_get_shared_first(
        "OPENAI_MODEL_CANDIDATES",
        default="gpt-5,gpt-4.1,gpt-4o,gpt-4o-mini",
    )
    llm_enabled_default: bool = _as_bool(_env_get_shared_first("LLM_ENABLED_DEFAULT", default="1"), True)
    llm_timeout_sec: float = _as_float(_env_get_shared_first("LLM_TIMEOUT_SEC", default="60"), 60.0)
    llm_max_input_chars: int = _as_int(_env_get_shared_first("LLM_MAX_INPUT_CHARS", default="18000"), 18000)
    llm_max_output_tokens: int = _as_int(
        _env_get_shared_first("LLM_MAX_OUTPUT_TOKENS", "OPENAI_MAX_TOKENS", default="2500"),
        2500,
    )

    # Polling and limits
    poll_interval_sec: float = _as_float(_env_get("TRANSCRIBE_POLL_INTERVAL", default="3"), 3.0)
    upload_timeout_sec: float = _as_float(_env_get("TRANSCRIBE_UPLOAD_TIMEOUT", default="300"), 300.0)
    request_timeout_sec: float = _as_float(_env_get("TRANSCRIBE_REQUEST_TIMEOUT", default="60"), 60.0)

    # Local auth
    auth_local_enabled: bool = _as_bool(_env_get_shared_first("AUTH_LOCAL_ENABLED", default="1"), True)
    auth_session_days: int = _as_int(_env_get_shared_first("AUTH_SESSION_DAYS", default="14"), 14)
    admin_login: str = _env_get_shared_first("ADMIN_LOGIN", default="Alex")
    admin_password: str = _env_get_shared_first("ADMIN_PASSWORD", default="change_me_strong_password")
    resource_tg_digest_url: str = _env_get_shared_first("RESOURCE_TG_DIGEST_URL", default="http://localhost:8010/setup")

    # Shared auth with TG Digest PostgreSQL
    auth_shared_enabled: bool = _as_bool(_env_get_shared_first("AUTH_SHARED_ENABLED", default="0"), False)
    auth_shared_cookie_name: str = _env_get_shared_first("AUTH_SHARED_COOKIE_NAME", default="session_token")
    auth_shared_register_url: str = _env_get_shared_first("AUTH_SHARED_REGISTER_URL", default="http://localhost:8010/register")
    auth_shared_login_url: str = _env_get_shared_first("AUTH_SHARED_LOGIN_URL", default="http://localhost:8010/login")
    auth_shared_admin_login: str = _env_get_shared_first("AUTH_SHARED_ADMIN_LOGIN", "ADMIN_LOGIN", default="Alex")
    auth_shared_pg_host: str = _env_get_shared_first("PGHOST", default="localhost")
    auth_shared_pg_port: int = _as_int(_env_get_shared_first("PGPORT", default="5432"), 5432)
    auth_shared_pg_database: str = _env_get_shared_first("PGDATABASE", default="tg_digest")
    auth_shared_pg_user: str = _env_get_shared_first("PGUSER", default="tg_digest")
    auth_shared_pg_password: str = _env_get_shared_first("PGPASSWORD", default="")

    # Audio retention: source uploads should be temporary by default
    keep_uploaded_audio: bool = _as_bool(_env_get_shared_first("KEEP_UPLOADED_AUDIO", default="0"), False)


settings = Settings()


def model_candidates() -> list[str]:
    values = [v.strip() for v in settings.openai_model_candidates_raw.split(",") if v.strip()]
    if settings.openai_model and settings.openai_model not in values:
        values.append(settings.openai_model)
    return values


def assemblyai_speech_models() -> list[str]:
    values = [v.strip() for v in settings.assemblyai_speech_models_raw.split(",") if v.strip()]
    return values or ["universal-2"]


def ensure_dirs() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.results_dir.mkdir(parents=True, exist_ok=True)
