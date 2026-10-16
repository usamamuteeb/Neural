"""Local audit trail and dashboard snapshot for Neural.

This module deliberately stores compact, redacted metadata rather than chat
transcripts or raw tool payloads.  It makes actions explainable without
turning a diagnostics feature into a second copy of private data or secrets.
"""

import json
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

from . import config

AUDIT_PATH = Path(__file__).resolve().parent.parent / "audit.jsonl"
MAX_AUDIT_BYTES = 5 * 1024 * 1024
MAX_RECENT_RECORDS = 2_000
_lock = threading.Lock()
_secret_key = re.compile(
    r"(?:api[_-]?key|authorization|cookie|password|secret|private[_-]?key|"
    r"(?:access|refresh|id|bearer)[_-]?token|^token$)",
    re.I,
)
_secret_value = re.compile(r"(?i)(bearer\s+)[^\s]+|(?:sk|pk|AIza)[-_A-Za-z0-9]{12,}")


def _redact(value: Any, depth: int = 0) -> Any:
    """Keep audit values useful while excluding secrets and large content."""
    if depth > 4:
        return "[truncated]"
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _secret_key.search(str(key)) else _redact(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return _secret_value.sub(r"\1[redacted]", value)[:500]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)[:500]


def _tool_category(tool: str) -> str:
    name = tool.lower()
    if tool in {"Bash", "Read", "Write", "Edit", "Grep", "Glob"}:
        return "shell" if tool == "Bash" else "files"
    if "browser" in name or "playwright" in name:
        return "browser"
    if "gmail" in name or "calendar" in name:
        return "google"
    if "buffer" in name or "post" in name:
        return "publishing"
    if "memory" in name:
        return "memory"
    return "tool"


def _summary(event: dict[str, Any]) -> str:
    event_type = event.get("type", "event")
    if event_type == "tool_use":
        return f"{event.get('tool', 'tool')} requested"
    if event_type == "tool_result":
        return "tool completed" if not event.get("is_error") else "tool failed"
    if event_type in ("automation_run", "background_job"):
        return f"{event.get('name', 'Automation')} {event.get('status', '')}".strip()
    if event_type == "turn_result":
        return "agent turn completed" if not event.get("is_error") else "agent turn failed"
    if event_type == "utility_call":
        provider = str(event.get("provider", "utility")).title()
        task = str(event.get("task", "task")).replace("_", " ")
        return f"{provider} utility {task} " + ("failed" if event.get("is_error") else "completed")
    if event_type == "message" and event.get("role") == "assistant":
        return str(event.get("text", "")).strip()[:240]
    return str(event_type).replace("_", " ")[:240]


def _record_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = event.get("type")
    # User messages can contain highly personal content and are not needed to
    # explain what Neural did. Assistant messages are retained as compact
    # decision summaries instead.
    if event_type == "message" and event.get("role") != "assistant":
        return None

    record = {
        "ts": event.get("ts", time.time()),
        "type": event_type or "event",
        "source": event.get("source", "chat"),
        "tool": event.get("tool"),
        "category": _tool_category(str(event.get("tool", ""))) if event.get("tool") else "agent",
        "status": "error" if event.get("is_error") or event.get("status") == "error" or event.get("is_error") is True else "ok",
        "summary": _summary(event),
    }
    if event_type == "tool_use":
        record["input"] = _redact(event.get("input", {}))
        record["tool_use_id"] = event.get("tool_use_id")
    if event_type == "tool_result":
        record["tool_use_id"] = event.get("tool_use_id")
    if event_type == "turn_result":
        record["cost_usd"] = event.get("cost_usd")
        record["usage"] = _redact(event.get("usage") or {})
        record["duration_ms"] = event.get("duration_ms")
    if event_type == "utility_call":
        record["provider"] = event.get("provider")
        record["model"] = event.get("model")
        record["task"] = event.get("task")
        record["cost_usd"] = event.get("cost_usd")
        record["usage"] = _redact(event.get("usage") or {})
        record["duration_ms"] = event.get("duration_ms")
    return record


def record_event(event: dict[str, Any]) -> None:
    """Append one sanitized audit record. Diagnostics must never break chat."""
    record = _record_from_event(event)
    if record is None:
        return
    try:
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _lock:
            if AUDIT_PATH.exists() and AUDIT_PATH.stat().st_size + len(encoded.encode("utf-8")) > MAX_AUDIT_BYTES:
                lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()[-MAX_RECENT_RECORDS // 2 :]
                AUDIT_PATH.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            with AUDIT_PATH.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
    except OSError:
        # A full disk or temporary file lock should never interrupt the agent.
        return


def recent_records(limit: int = 80) -> list[dict[str, Any]]:
    if not AUDIT_PATH.exists():
        return []
    try:
        rows = AUDIT_PATH.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, MAX_RECENT_RECORDS)) :]
    except OSError:
        return []
    records = []
    for row in rows:
        try:
            records.append(json.loads(row))
        except json.JSONDecodeError:
            continue
    return records


def dashboard_snapshot() -> dict[str, Any]:
    """Return compact, non-secret dashboard data for the local UI."""
    records = recent_records(500)
    tools = [record.get("tool") for record in records if record.get("type") == "tool_use" and record.get("tool")]
    failures = [record for record in records if record.get("status") == "error"][-12:]
    started = {
        record.get("summary", "").removesuffix(" started")
        for record in records
        if record.get("type") in ("automation_run", "background_job") and record.get("summary", "").endswith(" started")
    }
    finished = {
        record.get("summary", "").rsplit(" ", 1)[0]
        for record in records
        if record.get("type") in ("automation_run", "background_job")
        and record.get("summary", "").endswith((" completed", " error", " cancelled"))
    }
    costs = [float(record["cost_usd"]) for record in records if isinstance(record.get("cost_usd"), (int, float))]
    token_totals = Counter()
    for record in records:
        usage = record.get("usage") or {}
        if not isinstance(usage, dict):
            continue
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            if isinstance(usage.get(key), (int, float)):
                token_totals[key] += int(usage[key])
    utility_calls = [record for record in records if record.get("type") == "utility_call"]
    utility_by_provider = Counter(str(record.get("provider", "unknown")) for record in utility_calls)
    configured = {
        "Claude": {"status": "ready", "detail": "local CLI session"},
        "Qwen utility": {
            "status": "ready" if config.QWEN_ENABLED else "not configured",
            "detail": config.QWEN_UTILITY_MODEL if config.QWEN_ENABLED else "optional sidecar",
        },
        "Google": {"status": "configured" if config.GOOGLE_WORKSPACE_ENABLED else "not configured"},
        "Buffer": {"status": "configured" if config.BUFFER_ENABLED else "not configured"},
        "Adzuna": {"status": "configured" if config.ADZUNA_ENABLED else "not configured"},
        "Pexels": {"status": "configured" if config.PEXELS_ENABLED else "not configured"},
        "Video editor": {"status": "enabled" if config.VIDEO_EDITOR_ENABLED else "disabled"},
        "Cap": {"status": "enabled" if config.CAP_ENABLED else "disabled"},
    }
    return {
        "generated_at": time.time(),
        "active_jobs": sorted(started - finished),
        "health": configured,
        "usage": {
            "audited_actions": len(records),
            "tool_calls": len(tools),
            "top_tools": Counter(tools).most_common(5),
            "recorded_cost_usd": round(sum(costs), 4),
            "tokens": {
                "input": token_totals["input_tokens"],
                "output": token_totals["output_tokens"],
                "cache_read": token_totals["cache_read_input_tokens"],
                "cache_written": token_totals["cache_creation_input_tokens"],
            },
        },
        "utility": {
            "selected": config.UTILITY_PROVIDER if config.UTILITY_PROVIDER != "auto" else ("qwen" if config.QWEN_ENABLED else "claude"),
            "calls": len(utility_calls),
            "by_provider": dict(utility_by_provider),
        },
        "failures": failures,
        "decisions": [record for record in records if record.get("type") == "message"][-10:],
        "audit": records[-80:],
    }
