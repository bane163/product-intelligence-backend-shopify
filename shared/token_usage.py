"""Normalize token usage emitted by supported agent and model clients."""

from typing import Any


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    names = ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens",
             "completion_tokens", "input_token_count", "output_token_count",
             "total_token_count", "prompt_token_count", "completion_token_count")
    return {name: getattr(value, name) for name in names if hasattr(value, name)}


def normalize_token_usage(value: Any) -> dict[str, int]:
    usage = _mapping(value)
    details = usage.get("usage_details") if isinstance(usage, dict) else None
    if details is None and value is not None:
        details = getattr(value, "usage_details", None)
    detail_usage = _mapping(details)
    if detail_usage:
        usage = {**detail_usage, **usage}

    input_tokens = _int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("input_token_count")
        or usage.get("prompt_token_count")
    )
    output_tokens = _int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("output_token_count")
        or usage.get("completion_token_count")
    )
    total_tokens = _int(usage.get("total_tokens") or usage.get("total_token_count"))
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
