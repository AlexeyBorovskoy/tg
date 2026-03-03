from __future__ import annotations

import asyncio
import importlib
import random
from pathlib import Path
from typing import Any

import httpx

from .settings import assemblyai_speech_models, settings


class TranscriptionError(RuntimeError):
    pass


def is_supported_audio(filename: str) -> bool:
    return filename.lower().endswith((".mp3", ".wav", ".m4a", ".webm", ".ogg", ".mp4", ".aac"))


async def transcribe_audio(
    file_path: Path,
    diarization: bool,
    speakers_expected: int | None,
    language_code: str = "ru",
    boost_words: list[str] | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    provider_name = (provider or settings.provider or "").strip().lower()
    if provider_name == "mock":
        await asyncio.sleep(0.3)
        return _mock_result(file_path.name)

    if provider_name == "local_whisper":
        return await _local_whisper_transcribe(
            file_path=file_path,
            language_code=language_code,
            boost_words=boost_words,
        )

    if provider_name != "assemblyai":
        raise TranscriptionError(f"Unsupported provider: {provider_name}")

    if not settings.assemblyai_api_key:
        raise TranscriptionError("ASSEMBLYAI_API_KEY is not set")

    upload_url = await _assembly_upload(file_path)
    transcript_id = await _assembly_start_job(
        upload_url,
        diarization,
        speakers_expected,
        language_code,
        boost_words=boost_words,
    )
    return await _assembly_wait_result(transcript_id)


def _load_whisper_model():
    try:
        fw = importlib.import_module("faster_whisper")
    except Exception as exc:
        raise TranscriptionError(
            "local_whisper backend requires package 'faster-whisper'. Install dependencies first."
        ) from exc

    model_cls = getattr(fw, "WhisperModel", None)
    if model_cls is None:
        raise TranscriptionError("faster-whisper package is installed but WhisperModel is unavailable")
    return model_cls(
        settings.whisper_model_size,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        cpu_threads=settings.whisper_cpu_threads,
    )


_WHISPER_MODEL = None


def _get_whisper_model():
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        _WHISPER_MODEL = _load_whisper_model()
    return _WHISPER_MODEL


def _local_whisper_transcribe_sync(
    file_path: Path,
    language_code: str,
    boost_words: list[str] | None = None,
) -> dict[str, Any]:
    model = _get_whisper_model()
    initial_prompt = ""
    if boost_words:
        clean = [str(x).strip() for x in boost_words if str(x).strip()]
        if clean:
            initial_prompt = "Термины: " + ", ".join(clean[:120])

    segments, info = model.transcribe(
        str(file_path),
        language=language_code,
        beam_size=settings.whisper_beam_size,
        vad_filter=settings.whisper_vad_filter,
        word_timestamps=False,
        initial_prompt=initial_prompt or None,
    )

    utterances: list[dict[str, Any]] = []
    plain_parts: list[str] = []
    for seg in segments:
        text = str(getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        start_s = float(getattr(seg, "start", 0.0) or 0.0)
        end_s = float(getattr(seg, "end", start_s) or start_s)
        utterances.append(
            {
                "speaker": "SPEAKER_00",
                "start": int(start_s * 1000),
                "end": int(end_s * 1000),
                "text": text,
            }
        )
        plain_parts.append(text)

    duration_sec = float(getattr(info, "duration", 0.0) or 0.0)
    return {
        "status": "completed",
        "text": " ".join(plain_parts).strip(),
        "audio_duration": int(duration_sec),
        "utterances": utterances,
        "metadata": {
            "provider": "local_whisper",
            "source": file_path.name,
            "model_size": settings.whisper_model_size,
            "language": language_code,
            "duration_sec": duration_sec,
        },
    }


async def _local_whisper_transcribe(
    file_path: Path,
    language_code: str,
    boost_words: list[str] | None = None,
) -> dict[str, Any]:
    return await asyncio.to_thread(_local_whisper_transcribe_sync, file_path, language_code, boost_words)


async def _assembly_upload(file_path: Path) -> str:
    async with httpx.AsyncClient(timeout=settings.upload_timeout_sec) as client:
        with file_path.open("rb") as f:
            payload = f.read()
        resp = await client.post(
            "https://api.assemblyai.com/v2/upload",
            headers={"authorization": settings.assemblyai_api_key},
            content=payload,
        )
        if resp.status_code >= 400:
            raise TranscriptionError(f"Upload failed: {resp.status_code} {resp.text}")
        body = resp.json()
        url = body.get("upload_url")
        if not url:
            raise TranscriptionError("AssemblyAI upload response does not contain upload_url")
        return str(url)


async def _assembly_start_job(
    upload_url: str,
    diarization: bool,
    speakers_expected: int | None,
    language_code: str,
    boost_words: list[str] | None = None,
) -> str:
    cfg: dict[str, Any] = {
        "audio_url": upload_url,
        "language_code": language_code,
        "speech_models": assemblyai_speech_models(),
    }
    if diarization:
        cfg["speaker_labels"] = True
    if speakers_expected and speakers_expected > 0:
        cfg["speakers_expected"] = speakers_expected
    if boost_words:
        clean = []
        seen: set[str] = set()
        for w in boost_words:
            term = str(w).strip()
            if not term or term in seen:
                continue
            seen.add(term)
            clean.append(term[:64])
            if len(clean) >= 200:
                break
        if clean:
            cfg["word_boost"] = clean
            cfg["boost_param"] = "high"

    async with httpx.AsyncClient(timeout=settings.request_timeout_sec) as client:
        resp = await client.post(
            "https://api.assemblyai.com/v2/transcript",
            headers={
                "authorization": settings.assemblyai_api_key,
                "content-type": "application/json",
            },
            json=cfg,
        )
        if resp.status_code >= 400:
            raise TranscriptionError(f"Create transcript failed: {resp.status_code} {resp.text}")
        body = resp.json()
        tid = body.get("id")
        if not tid:
            raise TranscriptionError("AssemblyAI transcript response does not contain id")
        return str(tid)


async def _assembly_wait_result(transcript_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=settings.request_timeout_sec) as client:
        while True:
            resp = await client.get(
                f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
                headers={"authorization": settings.assemblyai_api_key},
            )
            if resp.status_code >= 400:
                raise TranscriptionError(f"Poll failed: {resp.status_code} {resp.text}")
            body = resp.json()
            status = body.get("status")
            if status == "completed":
                return body
            if status == "error":
                raise TranscriptionError(str(body.get("error") or "AssemblyAI error"))
            await asyncio.sleep(settings.poll_interval_sec)


def _mock_result(filename: str) -> dict[str, Any]:
    return {
        "status": "completed",
        "text": "Это тестовая расшифровка для локального запуска без внешнего API.",
        "audio_duration": random.randint(40, 140),
        "utterances": [
            {
                "speaker": "SPEAKER_00",
                "start": 0,
                "end": 3200,
                "text": "Коллеги, начинаем краткое совещание по статусу релиза.",
            },
            {
                "speaker": "SPEAKER_01",
                "start": 3300,
                "end": 7800,
                "text": "По интеграции с Telegram проблемы не выявлены, нужна проверка нагрузки.",
            },
            {
                "speaker": "SPEAKER_00",
                "start": 8000,
                "end": 11000,
                "text": "Принято, фиксируем задачу и сроки до пятницы.",
            },
        ],
        "metadata": {"provider": "mock", "source": filename},
    }
