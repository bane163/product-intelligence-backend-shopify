"""Compatibility helpers delegated to supabase service."""

from typing import Any

from app_context import get_ctx


def create_or_update_run(run_id: str, fields: dict[str, Any]) -> None:
    get_ctx().supabase.runs.create_or_update_run(run_id, fields)


def append_run_event(run_id: str, event: dict[str, Any], seq: int) -> None:
    get_ctx().supabase.runs.append_run_event(run_id, event, seq)


def append_run_message(
    run_id: str,
    *,
    role: str,
    message: Any,
    seq: int,
    meta: dict[str, Any] | None = None,
) -> None:
    get_ctx().supabase.runs.append_run_message(
        run_id, role=role, message=message, seq=seq, meta=meta
    )


def finalize_run(
    run_id: str,
    *,
    status: str,
    duration_ms: int | None = None,
    error: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> None:
    get_ctx().supabase.runs.finalize_run(
        run_id,
        status=status,
        duration_ms=duration_ms,
        error=error,
        extra_fields=extra_fields,
    )


def list_runs(limit: int = 50, offset: int = 0, status: str | None = None) -> list[dict[str, Any]]:
    return get_ctx().supabase.runs.list_runs(limit=limit, offset=offset, status=status)


def get_run(run_id: str) -> dict[str, Any] | None:
    return get_ctx().supabase.runs.get_run(run_id)


def get_run_history(run_id: str) -> dict[str, Any]:
    return get_ctx().supabase.runs.get_run_history(run_id)
