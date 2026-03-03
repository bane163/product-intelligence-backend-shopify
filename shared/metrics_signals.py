from __future__ import annotations

import json
import logging
from typing import Any

from shared.observability import current_observability_fields

LOG = logging.getLogger("metrics.signals")

_LEVELS: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def _sanitize(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return str(value)


def emit_metric_signal(event: str, *, level: str = "info", **fields: Any) -> None:
    event_name = str(event or "").strip().lower() or "unknown"
    payload: dict[str, Any] = {"event": event_name}
    payload.update(current_observability_fields())
    for key, value in fields.items():
        if value is None:
            continue
        payload[str(key)] = _sanitize(value)

    LOG.log(
        _LEVELS.get(str(level or "").strip().lower(), logging.INFO),
        "metric_signal %s",
        json.dumps(payload, sort_keys=True, default=str),
    )


def signal_api_error(
    *,
    route: str,
    method: str,
    status_code: int,
    error: str,
    **fields: Any,
) -> None:
    emit_metric_signal(
        "api.error",
        level="error",
        route=route,
        method=str(method or "").strip().upper() or "UNKNOWN",
        status_code=int(status_code),
        error=error,
        **fields,
    )


def signal_offload_queue(
    *,
    queue_name: str,
    status: str,
    backlog: int | None = None,
    job_id: str | None = None,
    job_type: str | None = None,
    attempt_count: int | None = None,
    max_attempts: int | None = None,
    run_id: str | None = None,
    error: str | None = None,
) -> None:
    normalized_status = str(status or "").strip().lower() or "unknown"
    emit_metric_signal(
        "offload.queue",
        level="warning" if normalized_status in {"retryable", "failed"} else "info",
        queue_name=queue_name,
        status=normalized_status,
        backlog=backlog,
        job_id=job_id,
        job_type=job_type,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        run_id=run_id,
        error=error,
    )


def signal_worker_job_failure(
    *,
    queue_name: str,
    job_id: str,
    job_type: str,
    run_id: str | None,
    dead_letter: bool,
    attempt_count: int,
    max_attempts: int,
    error: str,
) -> None:
    emit_metric_signal(
        "offload.worker_job",
        level="error" if dead_letter else "warning",
        queue_name=queue_name,
        job_id=job_id,
        job_type=job_type,
        run_id=run_id,
        dead_letter=dead_letter,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        error=error,
    )


def signal_shopify_retry(
    *,
    shop: str | None,
    attempt: int,
    max_attempts: int,
    reason: str,
    retry_after_seconds: float | None = None,
    error: str | None = None,
) -> None:
    emit_metric_signal(
        "shopify.retry",
        level="warning",
        shop=shop,
        attempt=attempt,
        max_attempts=max_attempts,
        reason=reason,
        retry_after_seconds=retry_after_seconds,
        error=error,
    )


def signal_llm_latency(
    *,
    operation: str,
    status: str,
    duration_ms: int,
    model_provider: str | None = None,
    model_id: str | None = None,
    error: str | None = None,
) -> None:
    normalized_status = str(status or "").strip().lower() or "unknown"
    emit_metric_signal(
        "llm.request",
        level="error" if normalized_status == "failed" else "info",
        operation=operation,
        status=normalized_status,
        duration_ms=duration_ms,
        model_provider=model_provider,
        model_id=model_id,
        error=error,
    )
