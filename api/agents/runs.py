"""Run tracing and orchestration endpoints."""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app_context import AppContext, get_ctx
from shared.observability import current_observability_fields
from .utils import require_shop_domain

router = APIRouter()

CANONICAL_RUN_STATUSES = {"queued", "running", "succeeded", "failed", "cancelled"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_run_status(value: Any, fallback: str = "running") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"queued", "pending", "created"}:
        return "queued"
    if normalized in {"running", "in_progress", "processing"}:
        return "running"
    if normalized in {"succeeded", "success", "completed", "complete", "done"}:
        return "succeeded"
    if normalized in {"failed", "error", "errored"}:
        return "failed"
    if normalized in {"cancelled", "canceled", "aborted"}:
        return "cancelled"
    return fallback


def _to_attempt(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        return 1
    return parsed if parsed > 0 else 1


def _append_control_event(
    *,
    ctx: AppContext,
    run_id: str,
    shop_domain: str,
    phase: str,
    message: str,
    metadata: dict[str, Any],
) -> None:
    enriched_metadata = dict(metadata or {})
    for key, value in current_observability_fields().items():
        enriched_metadata.setdefault(key, value)
    event = ctx.services.tracing.emit_run_event(
        run_id,
        phase=phase,
        message=message,
        metadata=enriched_metadata or None,
    )
    history = ctx.supabase.runs.get_run_history(run_id, shop_domain=shop_domain)
    next_seq = len(history.get("events") or []) + 1
    ctx.supabase.runs.append_run_event(run_id, event, next_seq)


def _validate_control_operation(operation: str) -> str:
    normalized = operation.strip().lower()
    if normalized not in {"cancel", "retry", "resume"}:
        raise HTTPException(status_code=400, detail="Unsupported run operation")
    return normalized


@router.get("/runs/{run_id}/events", summary="Stream live workflow events")
async def stream_events(
    run_id: str,
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> StreamingResponse:
    from application.use_cases.runs.get_run import execute as get_run_execute
    from application.use_cases.runs.stream_run_events import (
        execute as stream_events_execute,
    )

    tenant = require_shop_domain(request, shop_domain)
    run = get_run_execute(supabase=ctx.supabase, run_id=run_id, shop_domain=tenant)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    return StreamingResponse(
        stream_events_execute(tracing=ctx.services.tracing, run_id=run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/runs/{run_id}/event-snapshot",
    summary="Get persisted incremental workflow events",
)
async def get_event_snapshot(
    run_id: str,
    request: Request,
    after_seq: int = 0,
    limit: int = 200,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.runs.get_event_snapshot import execute

    tenant = require_shop_domain(request, shop_domain)
    snapshot = execute(
        supabase=ctx.supabase,
        run_id=run_id,
        shop_domain=tenant,
        after_seq=max(0, after_seq),
        limit=limit,
    )
    if snapshot["status"] == "unavailable":
        raise HTTPException(status_code=404, detail="Run not found")
    return snapshot

@router.get("/runs", summary="List persisted LLM runs")
async def list_llm_runs(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, list[dict]]:
    from application.use_cases.runs.list_runs import execute as list_runs_execute

    tenant = require_shop_domain(request, shop_domain)
    normalized_status = _normalize_run_status(status, "") if status else None
    if status and normalized_status not in CANONICAL_RUN_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid run status filter")

    return {
        "runs": list_runs_execute(
            supabase=ctx.supabase,
            limit=limit,
            offset=offset,
            status=normalized_status,
            shop_domain=tenant,
        )
    }


@router.get("/runs/{run_id}", summary="Get persisted LLM run")
async def get_llm_run(
    run_id: str,
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, dict]:
    from application.use_cases.runs.get_run import execute as get_run_execute

    tenant = require_shop_domain(request, shop_domain)
    run = get_run_execute(supabase=ctx.supabase, run_id=run_id, shop_domain=tenant)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run": run}


@router.delete("/runs/{run_id}", summary="Delete persisted LLM run")
async def delete_llm_run(
    run_id: str,
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.runs.delete_run import execute as delete_run_execute
    from application.use_cases.runs.get_run import execute as get_run_execute

    tenant = require_shop_domain(request, shop_domain)
    run = get_run_execute(supabase=ctx.supabase, run_id=run_id, shop_domain=tenant)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    current_status = _normalize_run_status(run.get("status"), "running")
    if current_status in {"queued", "running"}:
        raise HTTPException(
            status_code=409,
            detail=f"Run cannot be deleted from status '{current_status}'",
        )

    deleted = delete_run_execute(supabase=ctx.supabase, run_id=run_id, shop_domain=tenant)
    if not deleted:
        raise HTTPException(status_code=404, detail="Run not found")

    return {"ok": True, "run_id": run_id}


@router.get("/runs/{run_id}/history", summary="Get persisted LLM run history")
async def get_llm_run_history(
    run_id: str,
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.runs.get_run_history import (
        execute as get_run_history_execute,
    )

    tenant = require_shop_domain(request, shop_domain)
    history = get_run_history_execute(
        supabase=ctx.supabase,
        run_id=run_id,
        shop_domain=tenant,
    )
    if history.get("run") is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return history


@router.get(
    "/runs/{run_id}/diagnostics",
    summary="Get tenant-scoped run diagnostics",
)
async def get_llm_run_diagnostics(
    run_id: str,
    request: Request,
    event_limit: int = 200,
    message_limit: int = 200,
    offload_limit: int = 20,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.runs.get_run_diagnostics import (
        execute as get_run_diagnostics_execute,
    )

    tenant = require_shop_domain(request, shop_domain)
    diagnostics = get_run_diagnostics_execute(
        supabase=ctx.supabase,
        run_id=run_id,
        shop_domain=tenant,
        event_limit=event_limit,
        message_limit=message_limit,
        offload_limit=offload_limit,
    )
    if diagnostics.get("run") is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return diagnostics


@router.get("/runs/{run_id}/snapshot", summary="Get workflow snapshot for realtime")
async def get_workflow_snapshot(
    run_id: str,
    request: Request,
    draft_id: str | None = None,
    after_seq: int = 0,
    event_limit: int = 200,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.runs.get_workflow_snapshot import (
        execute as get_snapshot_execute,
    )

    tenant = require_shop_domain(request, shop_domain)
    snapshot = get_snapshot_execute(
        supabase=ctx.supabase,
        run_id=run_id,
        shop_domain=tenant,
        draft_id=draft_id,
        after_seq=after_seq,
        event_limit=event_limit,
    )
    if snapshot.get("run") is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return snapshot


@router.post("/runs/{run_id}/{operation}", summary="Control run lifecycle")
async def control_llm_run(
    run_id: str,
    operation: str,
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.runs.get_run import execute as get_run_execute

    normalized_operation = _validate_control_operation(operation)
    payload = await request.form()
    resume_token = str(payload.get("resume_token") or "").strip()
    tenant = require_shop_domain(request, shop_domain or payload.get("shop_domain"))

    run = get_run_execute(supabase=ctx.supabase, run_id=run_id, shop_domain=tenant)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if str(run.get("source") or "").strip().lower() == "file_upload":
        raise HTTPException(
            status_code=409,
            detail="Upload activities cannot be cancelled, retried, or resumed",
        )

    now = _utc_now_iso()
    current_status = _normalize_run_status(run.get("status"), "running")
    current_attempt = _to_attempt(run.get("attempt"))
    updates: dict[str, Any] = {"shop_domain": tenant, **current_observability_fields()}

    if normalized_operation == "cancel":
        if current_status not in {"queued", "running"}:
            raise HTTPException(
                status_code=409,
                detail=f"Run cannot be cancelled from status '{current_status}'",
            )
        updated = ctx.supabase.runs.cancel_run_cascade(run_id, tenant)
        if not updated:
            # Compatibility fallback for test/downgrade adapters; production persistence
            # uses the atomic RPC above.
            updates.update({"status": "cancelled", "ended_at": now,
                "failure_code": "cancelled_by_operator", "failure_message": "Run cancelled",
                "resume_token": None})
            _append_control_event(ctx=ctx, run_id=run_id, shop_domain=tenant,
                phase="run_cancelled", message="Run cancelled",
                metadata={"operation": normalized_operation, "attempt": current_attempt})
            ctx.supabase.runs.create_or_update_run(run_id, updates)
            updated = get_run_execute(supabase=ctx.supabase, run_id=run_id, shop_domain=tenant)
            if not updated:
                raise HTTPException(status_code=409, detail="Run state changed before cancellation")
        ctx.services.tracing.complete_run(run_id)
        return {"ok": True, "run_id": run_id, "operation": normalized_operation, "run": updated}
    elif normalized_operation == "retry":
        if current_status not in {"failed", "cancelled"}:
            raise HTTPException(
                status_code=409,
                detail=f"Run cannot be retried from status '{current_status}'",
            )
        updates.update(
            {
                "status": "queued",
                "attempt": current_attempt + 1,
                "started_at": now,
                "ended_at": None,
                "duration_ms": None,
                "error": None,
                "failure_code": None,
                "failure_message": None,
                "resume_token": None,
                "last_completed_step": None,
            }
        )
        _append_control_event(
            ctx=ctx,
            run_id=run_id,
            shop_domain=tenant,
            phase="run_retry_requested",
            message="Run retry requested",
            metadata={
                "operation": normalized_operation,
                "attempt": current_attempt + 1,
            },
        )
    else:  # resume
        if current_status != "failed":
            raise HTTPException(
                status_code=409,
                detail=f"Run cannot be resumed from status '{current_status}'",
            )
        if not resume_token:
            raise HTTPException(status_code=400, detail="Missing resume_token")
        persisted_token = str(run.get("resume_token") or "").strip()
        if persisted_token and persisted_token != resume_token:
            raise HTTPException(status_code=409, detail="resume_token mismatch")
        updates.update(
            {
                "status": "queued",
                "attempt": current_attempt + 1,
                "started_at": now,
                "ended_at": None,
                "duration_ms": None,
                "error": None,
                "failure_code": None,
                "failure_message": None,
                "resume_token": str(uuid4()),
            }
        )
        _append_control_event(
            ctx=ctx,
            run_id=run_id,
            shop_domain=tenant,
            phase="run_resume_requested",
            message="Run resume requested",
            metadata={
                "operation": normalized_operation,
                "attempt": current_attempt + 1,
                "last_completed_step": run.get("last_completed_step"),
            },
        )

    ctx.supabase.runs.create_or_update_run(run_id, updates)
    updated = get_run_execute(supabase=ctx.supabase, run_id=run_id, shop_domain=tenant)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update run")

    return {
        "ok": True,
        "run_id": run_id,
        "operation": normalized_operation,
        "run": updated,
    }
