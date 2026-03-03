from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from .settings import model_candidates, settings


@dataclass
class LLMResult:
    status: str
    text: str
    model_used: str | None = None
    error: str | None = None
    duration_ms: int | None = None


def _profile_text(source_kind: str, llm_profile: str | None) -> str:
    profile = (llm_profile or "").strip().lower()
    if profile in {"voice", "meeting"}:
        return profile
    if source_kind == "voice_message":
        return "voice"
    return "meeting"


def _default_messages(text: str, profile: str) -> list[dict[str, str]]:
    common = (
        "Ты редактор транскриптов на русском языке. "
        "Нужно улучшить читаемость, пунктуацию и орфографию без искажения смысла. "
        "Не добавляй новые факты, не удаляй важные детали."
    )
    if profile == "voice":
        system = (
            f"{common} Верни только итоговый очищенный текст без комментариев и пояснений. "
            "Если есть строки с префиксами времени/спикера, сохрани их."
        )
    else:
        system = (
            f"{common} Это протокол/совещание. Обязательно сохраняй структуру строк вида "
            "[мм:сс] Спикер: текст, если она присутствует. Верни только исправленный transcript."
        )
    user = (
        "Исправь transcript:\n\n"
        f"{text}\n\n"
        "Требование: верни только исправленный текст, без markdown-блоков и без вступлений."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _messages_from_prompt(text: str, profile: str, prompt_template: dict[str, Any]) -> list[dict[str, str]]:
    system = str(prompt_template.get("system_prompt") or "").strip()
    user_template = str(prompt_template.get("user_template") or "{transcript}").strip()

    user = user_template
    user = user.replace("{transcript}", text)
    user = user.replace("{profile}", profile)

    if "{transcript}" not in user_template and text not in user:
        user = f"{user}\n\n{text}"

    if not system:
        return _default_messages(text, profile)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


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


def _split_text_chunks(text: str, max_chars: int, overlap_lines: int = 3) -> list[str]:
    lines = text.splitlines()
    if not lines:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(lines)
    while start < n:
        size = 0
        end = start
        while end < n:
            next_len = len(lines[end]) + (1 if end > start else 0)
            if end > start and size + next_len > max_chars:
                break
            size += next_len
            end += 1
        if end == start:
            # Extremely long single line: hard cut.
            line = lines[start]
            chunks.append(line[:max_chars])
            start += 1
            continue
        chunks.append("\n".join(lines[start:end]).strip())
        if end >= n:
            break
        start = max(end - overlap_lines, start + 1)
    return [c for c in chunks if c]


def _remove_overlap_prefix(previous: str, current: str) -> str:
    prev_lines = [x for x in previous.splitlines() if x.strip()]
    cur_lines = [x for x in current.splitlines() if x.strip()]
    if not prev_lines or not cur_lines:
        return current
    max_check = min(5, len(prev_lines), len(cur_lines))
    for k in range(max_check, 0, -1):
        if prev_lines[-k:] == cur_lines[:k]:
            return "\n".join(cur_lines[k:]).strip()
    return current


async def _call_llm_with_candidates(
    messages: list[dict[str, str]],
    model_override: str | None,
) -> LLMResult:
    candidates: list[str] = []
    if model_override and model_override.strip():
        candidates.append(model_override.strip())
    for model in model_candidates():
        if model not in candidates:
            candidates.append(model)

    if not candidates:
        return LLMResult(status="skipped_no_model", text="")

    base_url = settings.openai_base_url.rstrip("/")
    endpoint = f"{base_url}/chat/completions"
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
                "temperature": 0.1,
                "max_tokens": settings.llm_max_output_tokens,
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

            duration_ms = int((time.monotonic() - t0) * 1000)
            return LLMResult(status="applied", text=text.strip(), model_used=model, duration_ms=duration_ms)

    duration_ms = int((time.monotonic() - t0) * 1000)
    return LLMResult(
        status="fallback_error",
        text="",
        error="; ".join(failures)[:1500] if failures else "unknown",
        duration_ms=duration_ms,
    )


async def run_llm_postprocess(
    transcript_text: str,
    source_kind: str,
    llm_profile: str | None,
    model_override: str | None,
    enabled: bool,
    prompt_template: dict[str, Any] | None = None,
) -> LLMResult:
    if not enabled:
        return LLMResult(status="disabled", text=transcript_text)
    if not transcript_text.strip():
        return LLMResult(status="skipped_empty", text=transcript_text)
    if not settings.openai_api_key:
        return LLMResult(status="skipped_no_key", text=transcript_text)

    profile = _profile_text(source_kind, llm_profile)
    limit = max(2000, settings.llm_max_input_chars)

    if len(transcript_text) <= limit:
        if prompt_template:
            messages = _messages_from_prompt(transcript_text, profile, prompt_template)
        else:
            messages = _default_messages(transcript_text, profile)
        result = await _call_llm_with_candidates(messages, model_override=model_override)
        if result.status == "applied":
            return result
        return LLMResult(
            status=result.status,
            text=transcript_text,
            model_used=result.model_used,
            error=result.error,
            duration_ms=result.duration_ms,
        )

    # Long transcript: process chunk-by-chunk to avoid truncation and preserve quality.
    chunks = _split_text_chunks(transcript_text, max_chars=limit, overlap_lines=3)
    out_chunks: list[str] = []
    combined_status = "applied_chunked"
    first_model_used: str | None = None
    all_errors: list[str] = []
    t0 = time.monotonic()

    for idx, chunk in enumerate(chunks):
        if prompt_template:
            messages = _messages_from_prompt(chunk, profile, prompt_template)
        else:
            messages = _default_messages(chunk, profile)
        chunk_res = await _call_llm_with_candidates(messages, model_override=model_override)
        if not first_model_used and chunk_res.model_used:
            first_model_used = chunk_res.model_used
        if chunk_res.status == "applied":
            cleaned = chunk_res.text.strip()
        else:
            combined_status = "partial_chunk_fallback"
            if chunk_res.error:
                all_errors.append(f"chunk{idx + 1}: {chunk_res.error}")
            cleaned = chunk.strip()
        if out_chunks:
            cleaned = _remove_overlap_prefix(out_chunks[-1], cleaned)
        if cleaned:
            out_chunks.append(cleaned)

    duration_ms = int((time.monotonic() - t0) * 1000)
    merged = "\n".join(out_chunks).strip()
    if not merged:
        return LLMResult(status="fallback_error", text=transcript_text, duration_ms=duration_ms)
    return LLMResult(
        status=combined_status,
        text=merged,
        model_used=first_model_used,
        error="; ".join(all_errors)[:1500] if all_errors else None,
        duration_ms=duration_ms,
    )
