"""Compatibility helpers delegated to tracing service."""

from app_context import get_ctx


def emit_run_event(
    run_id: str,
    *,
    phase: str,
    message: str,
    level: str = "info",
    payload_preview=None,
    error: str | None = None,
    metadata: dict | None = None,
) -> dict:
    return get_ctx().services.tracing.emit_run_event(
        run_id,
        phase=phase,
        message=message,
        level=level,
        payload_preview=payload_preview,
        error=error,
        metadata=metadata,
    )


def complete_run(run_id: str) -> None:
    get_ctx().services.tracing.complete_run(run_id)


async def stream_run_events(run_id: str):
    async for chunk in get_ctx().services.tracing.stream_run_events(run_id):
        yield chunk
