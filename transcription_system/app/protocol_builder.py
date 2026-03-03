from __future__ import annotations

import re
from datetime import datetime
from typing import Any


LINE_RE = re.compile(r"^\[(?P<ts>\d+:\d{2})\]\s*(?P<speaker>[^:]+):\s*(?P<text>.+)$")
DECISION_HINTS = (
    "решили",
    "приняли решение",
    "договорились",
    "утвердить",
    "согласовали",
    "зафиксировали",
)
ACTION_HINTS = (
    "поруч",
    "нужно",
    "надо",
    "сделать",
    "подготовить",
    "проверить",
    "отправить",
    "внести",
    "обновить",
)
DUE_RE = re.compile(
    r"\b(до\s+\d{1,2}\.\d{1,2}(?:\.\d{2,4})?|до\s+(понедельника|вторника|среды|четверга|пятницы|субботы|воскресенья)|"
    r"\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)\b",
    flags=re.IGNORECASE,
)


def _normalize_text(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text


def _split_lines(transcript_text: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in (transcript_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = LINE_RE.match(line)
        if not m:
            out.append({"ts": "0:00", "speaker": "Неизвестно", "text": line})
            continue
        out.append(
            {
                "ts": m.group("ts"),
                "speaker": _normalize_text(m.group("speaker")),
                "text": _normalize_text(m.group("text")),
            }
        )
    return out


def _citation(row: dict[str, str]) -> str:
    return f"[{row['ts']}] {row['speaker']}: {row['text']}"


def _extract_due_date(text: str) -> str | None:
    m = DUE_RE.search(text or "")
    if not m:
        return None
    return _normalize_text(m.group(0))


def _extract_decisions(rows: list[dict[str, str]], limit: int = 12) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        low = row["text"].lower()
        if not any(hint in low for hint in DECISION_HINTS):
            continue
        out.append(
            {
                "text": row["text"],
                "speaker": row["speaker"],
                "timestamp": row["ts"],
                "citation": _citation(row),
            }
        )
        if len(out) >= limit:
            break
    return out


def _extract_action_items(rows: list[dict[str, str]], limit: int = 20) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        low = row["text"].lower()
        if not any(hint in low for hint in ACTION_HINTS):
            continue
        task = row["text"]
        due_date = _extract_due_date(task)
        item = {
            "task": task,
            "owner": row["speaker"],
            "due_date": due_date,
            "timestamp": row["ts"],
            "citation": _citation(row),
        }
        key = (item["owner"], item["task"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _extract_key_points(rows: list[dict[str, str]], limit: int = 15) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        text = row["text"]
        if len(text) < 25:
            continue
        out.append(
            {
                "text": text,
                "speaker": row["speaker"],
                "timestamp": row["ts"],
                "citation": _citation(row),
            }
        )
        if len(out) >= limit:
            break
    return out


def _participants(rows: list[dict[str, str]]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for row in rows:
        speaker = row["speaker"]
        if speaker in seen:
            continue
        seen.add(speaker)
        ordered.append(speaker)
    return ordered


def build_meeting_protocol(
    transcript_text: str,
    source_filename: str,
) -> dict[str, Any]:
    rows = _split_lines(transcript_text)
    participants = _participants(rows)
    key_points = _extract_key_points(rows)
    decisions = _extract_decisions(rows)
    action_items = _extract_action_items(rows)

    return {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_filename": source_filename,
        "participants": participants,
        "key_points": key_points,
        "decisions": decisions,
        "action_items": action_items,
        "stats": {
            "line_count": len(rows),
            "participants_count": len(participants),
            "key_points_count": len(key_points),
            "decisions_count": len(decisions),
            "action_items_count": len(action_items),
        },
    }


def protocol_markdown(protocol: dict[str, Any]) -> str:
    participants = protocol.get("participants") or []
    key_points = protocol.get("key_points") or []
    decisions = protocol.get("decisions") or []
    action_items = protocol.get("action_items") or []
    stats = protocol.get("stats") or {}

    lines: list[str] = []
    lines.append("# Протокол совещания")
    lines.append("")
    lines.append(f"- Источник: `{protocol.get('source_filename') or 'n/a'}`")
    lines.append(f"- Сформировано: {protocol.get('generated_at') or 'n/a'}")
    lines.append(f"- Строк в стенограмме: {stats.get('line_count', 0)}")
    lines.append("")

    lines.append("## Участники")
    lines.append("")
    if participants:
        for name in participants:
            lines.append(f"- {name}")
    else:
        lines.append("- Не определены")
    lines.append("")

    lines.append("## Ключевые тезисы")
    lines.append("")
    if key_points:
        for idx, item in enumerate(key_points, start=1):
            lines.append(f"{idx}. {item.get('text')}")
            lines.append(f"   Основание: `{item.get('citation')}`")
    else:
        lines.append("1. Недостаточно данных для извлечения тезисов.")
    lines.append("")

    lines.append("## Решения")
    lines.append("")
    if decisions:
        for idx, item in enumerate(decisions, start=1):
            lines.append(f"{idx}. {item.get('text')}")
            lines.append(f"   Основание: `{item.get('citation')}`")
    else:
        lines.append("1. Явные формулировки решений не обнаружены.")
    lines.append("")

    lines.append("## Поручения")
    lines.append("")
    if action_items:
        lines.append("| № | Что сделать | Ответственный | Срок | Подтверждение |")
        lines.append("|---|---|---|---|---|")
        for idx, item in enumerate(action_items, start=1):
            lines.append(
                f"| {idx} | {item.get('task') or ''} | {item.get('owner') or ''} | "
                f"{item.get('due_date') or '—'} | {item.get('citation') or ''} |"
            )
    else:
        lines.append("Поручения не выделены.")
    lines.append("")
    return "\n".join(lines).strip() + "\n"
