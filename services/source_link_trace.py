"""Best-effort, development-only source-link diagnostics."""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any
from uuid import UUID

LOG = logging.getLogger(__name__)

_SAFE_DETAIL_KEYS = {
    "error",
    "event",
    "file_kind",
    "origin",
    "provider",
    "range_count",
    "reason",
    "sheet",
    "state",
    "status_code",
    "target",
    "url_host",
}


def enabled() -> bool:
    explicit = os.getenv("SOURCE_LINK_TRACE_ENABLED", "").strip().lower()
    if explicit:
        return explicit in {"1", "true", "yes", "on"}
    if os.getenv("DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return os.getenv("ENVIRONMENT", "").strip().lower() in {"staging", "production"}


def sample_rate() -> float:
    raw = os.getenv("SOURCE_LINK_TRACE_SAMPLE_RATE", "").strip()
    if not raw:
        environment = os.getenv("ENVIRONMENT", "").strip().lower()
        return (
            1.0
            if environment in {"", "development", "debug", "staging", "test"}
            else 0.05
        )
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("SOURCE_LINK_TRACE_SAMPLE_RATE must be a number from 0 to 1") from exc
    if not 0 <= value <= 1:
        raise ValueError("SOURCE_LINK_TRACE_SAMPLE_RATE must be a number from 0 to 1")
    return value


def sampled(attempt_id: Any) -> bool:
    """Make one stable sampling decision for every event in an attempt."""
    normalized = valid_attempt_id(attempt_id)
    if not normalized:
        return False
    rate = sample_rate()
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    bucket = int.from_bytes(hashlib.sha256(normalized.encode()).digest()[:8], "big")
    return bucket / float(2**64) < rate


def valid_attempt_id(value: Any) -> str | None:
    try:
        return str(UUID(str(value))) if value else None
    except (TypeError, ValueError, AttributeError):
        return None


def _sanitize_details(details: dict[str, Any] | None) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in (details or {}).items():
        if key not in _SAFE_DETAIL_KEYS or value is None:
            continue
        if isinstance(value, (bool, int, float)):
            clean[key] = value
        elif isinstance(value, str):
            clean[key] = value[:500]
    return clean


def record(
    *,
    component: str,
    stage: str,
    status: str = "info",
    attempt_id: Any = None,
    shop_domain: str | None = None,
    run_id: str | None = None,
    source_file_id: str | None = None,
    highlight_file_id: str | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
    duration_ms: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Persist a sanitized event without ever breaking the user flow."""
    if not enabled() or not sampled(attempt_id):
        return
    payload = {
        "attempt_id": valid_attempt_id(attempt_id),
        "shop_domain": (shop_domain or "").strip().lower() or None,
        "run_id": run_id,
        "source_file_id": source_file_id,
        "highlight_file_id": highlight_file_id,
        "component": component[:80],
        "stage": stage[:120],
        "status": status[:40],
        "request_id": request_id,
        "correlation_id": correlation_id,
        "duration_ms": duration_ms,
        "details": _sanitize_details(details),
    }
    try:
        from supabase_client import get_supabase

        get_supabase().table("source_link_trace_events").insert(payload).execute()
    except Exception:
        LOG.debug("Could not persist source-link trace event", exc_info=True)


def prune() -> None:
    if not enabled():
        return
    try:
        days = max(1, int(os.getenv("SOURCE_LINK_TRACE_RETENTION_DAYS", "30")))
        from datetime import datetime, timedelta, timezone
        from supabase_client import get_supabase

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        get_supabase().table("source_link_trace_events").delete().lt(
            "created_at", cutoff
        ).execute()
    except Exception:
        LOG.debug("Could not prune source-link trace events", exc_info=True)
