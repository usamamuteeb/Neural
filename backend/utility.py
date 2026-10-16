"""Safe, one-shot utility-model adapter.

This is intentionally not an agent runtime.  A utility call accepts one text
prompt and returns one text result.  It has no tools, no persistent context,
no job/session state, and no access to the browser, shell, files, Gmail,
Buffer, or the main chat client.  That boundary lets Neural use Qwen for
cheap, stateless work without creating a second actor that could revive an
old render or produce delayed chat replies.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

from . import config
from .events import bus


class UtilityProviderError(RuntimeError):
    """Raised for a failed external utility request without exposing its payload."""


def _selected_provider() -> str:
    if config.UTILITY_PROVIDER == "claude":
        return "claude"
    if config.QWEN_ENABLED:
        return "qwen"
    return "claude"


def utility_status() -> dict[str, str | bool]:
    """Non-secret status suitable for the settings API and dashboard."""
    selected = _selected_provider()
    return {
        "selected": selected,
        "configured": config.QWEN_ENABLED,
        "qwen_model": config.QWEN_UTILITY_MODEL if config.QWEN_ENABLED else "",
        "claude_model": config.UTILITY_MODEL,
        "fallback": "claude" if selected == "qwen" else "none",
    }


def _usage(payload: dict[str, Any]) -> dict[str, int]:
    raw = payload.get("usage") or {}
    if not isinstance(raw, dict):
        return {}
    # The OpenAI-compatible response uses prompt/completion_tokens. Normalize
    # them so the existing dashboard can aggregate every provider uniformly.
    prompt = raw.get("prompt_tokens", raw.get("input_tokens"))
    completion = raw.get("completion_tokens", raw.get("output_tokens"))
    usage: dict[str, int] = {}
    if isinstance(prompt, (int, float)):
        usage["input_tokens"] = int(prompt)
    if isinstance(completion, (int, float)):
        usage["output_tokens"] = int(completion)
    return usage


def _qwen_cost(usage: dict[str, int]) -> float | None:
    if not (config.QWEN_INPUT_USD_PER_MILLION or config.QWEN_OUTPUT_USD_PER_MILLION):
        return None
    return round(
        (usage.get("input_tokens", 0) * config.QWEN_INPUT_USD_PER_MILLION
         + usage.get("output_tokens", 0) * config.QWEN_OUTPUT_USD_PER_MILLION) / 1_000_000,
        8,
    )


async def _qwen_task(prompt: str, task: str) -> str:
    if not config.QWEN_ENABLED:
        raise UtilityProviderError("Qwen is not configured")
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=config.UTILITY_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{config.QWEN_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {config.QWEN_API_KEY}"},
                json={
                    "model": config.QWEN_UTILITY_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    # Deliberately no `tools` field. This adapter must remain
                    # one request/one response, never an autonomous loop.
                },
            )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") or []
        message = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        if isinstance(content, list):
            content = "".join(
                item.get("text", "") for item in content if isinstance(item, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise UtilityProviderError("Qwen returned no text")
        usage = _usage(payload)
        bus.publish({
            "type": "utility_call",
            "provider": "qwen",
            "model": config.QWEN_UTILITY_MODEL,
            "task": task,
            "usage": usage,
            "cost_usd": _qwen_cost(usage),
            "duration_ms": round((time.perf_counter() - started) * 1000),
        })
        return content
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        # The dashboard needs the failure category, but never a response body
        # that might carry provider diagnostics or user content.
        bus.publish({
            "type": "utility_call",
            "provider": "qwen",
            "model": config.QWEN_UTILITY_MODEL,
            "task": task,
            "is_error": True,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        })
        raise UtilityProviderError("Qwen utility request failed") from exc


async def _claude_task(prompt: str, task: str) -> str:
    started = time.perf_counter()
    options = ClaudeAgentOptions(allowed_tools=[], env={"ANTHROPIC_MODEL": config.UTILITY_MODEL})
    text_parts: list[str] = []
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
        result = "".join(text_parts)
        bus.publish({
            "type": "utility_call",
            "provider": "claude",
            "model": config.UTILITY_MODEL,
            "task": task,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        })
        return result
    except Exception:
        bus.publish({
            "type": "utility_call",
            "provider": "claude",
            "model": config.UTILITY_MODEL,
            "task": task,
            "is_error": True,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        })
        raise


async def quick_task(prompt: str, *, task: str = "utility") -> str:
    """Run a non-agentic task with Qwen when available, then Claude fallback.

    Callers must pass only task-local text. Do not use this for chat replies,
    tool decisions, job progress, cancellation, or anything that can change
    state. A Qwen outage falls back to the pre-existing Claude behavior.
    """
    if _selected_provider() == "qwen":
        try:
            return await _qwen_task(prompt, task)
        except UtilityProviderError:
            return await _claude_task(prompt, task)
    return await _claude_task(prompt, task)
