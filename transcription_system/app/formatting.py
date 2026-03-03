from __future__ import annotations

import re
from datetime import datetime
from typing import Any


def smart_replace(text: str, glossary: dict[str, str]) -> tuple[str, list[str]]:
    replacements: list[str] = []
    out = text

    for wrong, correct in glossary.items():
        wrong = wrong.strip()
        correct = correct.strip()
        if not wrong or not correct:
            continue

        pattern = r"(?<![а-яёА-ЯЁa-zA-Z0-9_])" + re.escape(wrong) + r"(?![а-яёА-ЯЁa-zA-Z0-9_])"
        if len(wrong) <= 3:
            flags = 0
            if not (wrong.isupper() or (len(wrong) > 1 and wrong[0].isupper())):
                continue
        else:
            flags = re.IGNORECASE

        out, replaced_count = re.subn(pattern, correct, out, flags=flags)
        if replaced_count > 0:
            replacements.append(f"{wrong}->{correct} (x{replaced_count})")

    return out, replacements


def format_mmss(ms: int | float | None) -> str:
    if ms is None:
        return "0:00"
    iv = int(ms)
    minutes = iv // 60000
    sec = (iv % 60000) // 1000
    return f"{minutes}:{sec:02d}"


def speaker_label(raw_speaker: str, speaker_map: dict[str, str] | None = None) -> str:
    if speaker_map and raw_speaker in speaker_map:
        return speaker_map[raw_speaker]
    if raw_speaker.startswith("SPEAKER_"):
        suffix = raw_speaker.split("_", 1)[1]
        return f"Спикер {suffix}"
    return raw_speaker


def apply_glossary_to_result(result: dict[str, Any], glossary: dict[str, str]) -> dict[str, Any]:
    out = dict(result)
    if out.get("text"):
        out["text"], _ = smart_replace(str(out["text"]), glossary)

    utterances = out.get("utterances")
    if isinstance(utterances, list):
        patched: list[dict[str, Any]] = []
        for utt in utterances:
            row = dict(utt)
            row["text"], _ = smart_replace(str(row.get("text", "")), glossary)
            patched.append(row)
        out["utterances"] = patched
    return out


def apply_glossary_to_result_with_stats(
    result: dict[str, Any],
    glossary: dict[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    stats_map: dict[tuple[str, str], int] = {}
    out = dict(result)

    def _apply_with_count(text: str, count_enabled: bool = True) -> str:
        current = text
        for wrong, correct in glossary.items():
            wrong_clean = str(wrong).strip()
            correct_clean = str(correct).strip()
            if not wrong_clean or not correct_clean:
                continue
            pattern = r"(?<![а-яёА-ЯЁa-zA-Z0-9_])" + re.escape(wrong_clean) + r"(?![а-яёА-ЯЁa-zA-Z0-9_])"
            if len(wrong_clean) <= 3:
                flags = 0
                if not (wrong_clean.isupper() or (len(wrong_clean) > 1 and wrong_clean[0].isupper())):
                    continue
            else:
                flags = re.IGNORECASE
            current, replaced_count = re.subn(pattern, correct_clean, current, flags=flags)
            if count_enabled and replaced_count > 0:
                key = (wrong_clean, correct_clean)
                stats_map[key] = stats_map.get(key, 0) + replaced_count
        return current

    utterances = out.get("utterances")
    if isinstance(utterances, list):
        patched: list[dict[str, Any]] = []
        for utt in utterances:
            row = dict(utt)
            row["text"] = _apply_with_count(str(row.get("text", "")), count_enabled=True)
            patched.append(row)
        out["utterances"] = patched
        if out.get("text"):
            out["text"] = _apply_with_count(str(out["text"]), count_enabled=False)
    elif out.get("text"):
        out["text"] = _apply_with_count(str(out["text"]), count_enabled=True)

    stats: list[dict[str, Any]] = []
    for (wrong, correct), count in sorted(stats_map.items(), key=lambda x: (x[0][0], x[0][1])):
        stats.append({"wrong": wrong, "correct": correct, "count": int(count)})

    return out, stats


def transcript_text_from_result(result: dict[str, Any], speaker_map: dict[str, str] | None = None) -> str:
    utterances = result.get("utterances")
    if isinstance(utterances, list) and utterances:
        lines: list[str] = []
        for utt in utterances:
            speaker = speaker_label(str(utt.get("speaker", "?")), speaker_map)
            start = format_mmss(utt.get("start"))
            text = str(utt.get("text", "")).strip()
            if text:
                lines.append(f"[{start}] {speaker}: {text}")
        return "\n".join(lines)
    return str(result.get("text", "")).strip()


def transcript_markdown_from_result(
    result: dict[str, Any],
    source_filename: str,
    speaker_map: dict[str, str] | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# Транскрипт")
    lines.append("")
    lines.append(f"- Источник: `{source_filename}`")
    lines.append(f"- Сформировано: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    utterances = result.get("utterances")
    if isinstance(utterances, list) and utterances:
        lines.append("## Расшифровка по ролям")
        lines.append("")
        current_speaker: str | None = None
        for utt in utterances:
            raw = str(utt.get("speaker", "SPEAKER_UNK"))
            spk = speaker_label(raw, speaker_map)
            if spk != current_speaker:
                lines.append(f"### {spk}")
                lines.append("")
                current_speaker = spk
            start = format_mmss(utt.get("start"))
            text = str(utt.get("text", "")).strip()
            if text:
                lines.append(f"[{start}] {text}")
                lines.append("")
    else:
        lines.append("## Полный текст")
        lines.append("")
        lines.append(str(result.get("text", "")).strip())
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def transcript_markdown_from_text(
    transcript_text: str,
    source_filename: str,
    llm_status: str | None = None,
    llm_model_used: str | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# Транскрипт")
    lines.append("")
    lines.append(f"- Источник: `{source_filename}`")
    lines.append(f"- Сформировано: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if llm_status:
        lines.append(f"- LLM статус: `{llm_status}`")
    if llm_model_used:
        lines.append(f"- LLM модель: `{llm_model_used}`")
    lines.append("")
    lines.append("## Полный текст")
    lines.append("")
    lines.append((transcript_text or "").strip())
    lines.append("")
    return "\n".join(lines)
