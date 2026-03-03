from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .settings import model_candidates, settings


@dataclass
class RoleAssignmentResult:
    status: str
    speaker_map: dict[str, str]
    model_used: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    source: str = "none"


def _collect_speaker_samples(result: dict[str, Any], max_lines_per_speaker: int = 4) -> dict[str, list[str]]:
    utterances = result.get("utterances") or []
    by_speaker: dict[str, list[str]] = {}
    for utt in utterances:
        if not isinstance(utt, dict):
            continue
        speaker = str(utt.get("speaker") or "").strip()
        text = str(utt.get("text") or "").strip()
        if not speaker or not text:
            continue
        rows = by_speaker.setdefault(speaker, [])
        if len(rows) < max_lines_per_speaker:
            rows.append(text)
    return by_speaker


def _heuristic_map(speakers: list[str]) -> dict[str, str]:
    ordered = sorted(speakers)
    out: dict[str, str] = {}
    for idx, spk in enumerate(ordered, start=1):
        out[spk] = f"Спикер {idx}"
    return out


def _extract_error_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except Exception:
        return resp.text[:500]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        code = err.get("code") or "unknown"
        msg = err.get("message") or str(body)
        return f"{code}: {msg}"
    return str(body)[:500]


def _parse_json_map(raw_text: str, allowed_speakers: set[str]) -> dict[str, str] | None:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    out: dict[str, str] = {}
    for key, value in payload.items():
        k = str(key).strip()
        v = str(value).strip()
        if not k or not v:
            continue
        if k not in allowed_speakers:
            continue
        out[k] = v[:80]
    return out if out else None


async def _llm_map_speakers(
    speaker_samples: dict[str, list[str]],
    source_kind: str,
) -> RoleAssignmentResult:
    if not settings.openai_api_key:
        return RoleAssignmentResult(status="heuristic_no_key", speaker_map={}, source="heuristic")

    speakers = sorted(speaker_samples.keys())
    profile = "voice" if source_kind == "voice_message" else "meeting"

    blocks: list[str] = []
    for spk in speakers:
        samples = speaker_samples.get(spk, [])
        sample_text = " | ".join(samples)
        blocks.append(f"{spk}: {sample_text}")

    system = (
        "Ты анализируешь стенограмму и назначаешь человеку читаемые роли/имена спикеров. "
        "Нельзя придумывать факты. Если имя неочевидно, используй нейтральную роль вида "
        "'Спикер 1', 'Спикер 2'. Ответ строго JSON объектом без пояснений."
    )
    user = (
        f"Профиль: {profile}\n"
        "Нужно вернуть JSON map speaker_id -> role.\n"
        "Сохрани все исходные speaker_id как ключи.\n"
        f"Спикеры и реплики:\n{chr(10).join(blocks)}"
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    candidates = model_candidates()
    if not candidates:
        return RoleAssignmentResult(status="heuristic_no_model", speaker_map={}, source="heuristic")

    endpoint = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }

    failures: list[str] = []
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=settings.llm_timeout_sec) as client:
        for model in candidates:
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": 0.0,
                "max_tokens": 400,
            }
            try:
                resp = await client.post(endpoint, headers=headers, json=payload)
            except Exception as exc:
                failures.append(f"{model}: request_error {exc}")
                continue

            if resp.status_code >= 400:
                failures.append(f"{model}: http_{resp.status_code} {_extract_error_text(resp)}")
                continue

            try:
                body = resp.json()
                text = body["choices"][0]["message"]["content"]
            except Exception as exc:
                failures.append(f"{model}: bad_response {exc}")
                continue

            if not isinstance(text, str) or not text.strip():
                failures.append(f"{model}: empty_response")
                continue

            mapping = _parse_json_map(text, set(speakers))
            if not mapping:
                failures.append(f"{model}: invalid_json_map")
                continue

            duration_ms = int((time.monotonic() - t0) * 1000)
            return RoleAssignmentResult(
                status="applied",
                speaker_map=mapping,
                model_used=model,
                duration_ms=duration_ms,
                source="llm",
            )

    duration_ms = int((time.monotonic() - t0) * 1000)
    return RoleAssignmentResult(
        status="fallback_error",
        speaker_map={},
        error="; ".join(failures)[:1500] if failures else "unknown",
        duration_ms=duration_ms,
        source="heuristic",
    )


async def run_role_assignment(
    result: dict[str, Any],
    source_kind: str,
    enabled: bool,
    existing_map: dict[str, str] | None = None,
) -> RoleAssignmentResult:
    current = existing_map or {}
    if current:
        return RoleAssignmentResult(status="existing", speaker_map=current, source="user")
    if not enabled:
        return RoleAssignmentResult(status="disabled", speaker_map={}, source="none")

    samples = _collect_speaker_samples(result)
    speakers = sorted(samples.keys())
    if len(speakers) <= 1:
        return RoleAssignmentResult(status="skipped_single_speaker", speaker_map={}, source="none")

    llm_result = await _llm_map_speakers(samples, source_kind=source_kind)
    if llm_result.speaker_map:
        return llm_result

    # Always provide deterministic fallback labels to keep role mapping stable.
    fallback = _heuristic_map(speakers)
    if llm_result.status.startswith("heuristic"):
        return RoleAssignmentResult(
            status=llm_result.status,
            speaker_map=fallback,
            model_used=llm_result.model_used,
            error=llm_result.error,
            duration_ms=llm_result.duration_ms,
            source="heuristic",
        )
    return RoleAssignmentResult(
        status="heuristic_fallback",
        speaker_map=fallback,
        model_used=llm_result.model_used,
        error=llm_result.error,
        duration_ms=llm_result.duration_ms,
        source="heuristic",
    )
