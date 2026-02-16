"""Run tracing endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app_context import AppContext, get_ctx

router = APIRouter()


@router.get("/runs/{run_id}/events", summary="Stream live workflow events")
async def stream_events(run_id: str, ctx: AppContext = Depends(get_ctx)) -> StreamingResponse:
    from application.use_cases.stream_run_events import execute as stream_events_execute
    return StreamingResponse(
        stream_events_execute(tracing=ctx.services.tracing, run_id=run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs", summary="List persisted LLM runs")
async def list_llm_runs(
    limit: int = 50, offset: int = 0, status: str | None = None, ctx: AppContext = Depends(get_ctx)
) -> dict[str, list[dict]]:
    from application.use_cases.list_runs import execute as list_runs_execute
    return {"runs": list_runs_execute(supabase=ctx.services.supabase, limit=limit, offset=offset, status=status)}


@router.get("/runs/{run_id}", summary="Get persisted LLM run")
async def get_llm_run(run_id: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, dict]:
    from application.use_cases.get_run import execute as get_run_execute
    run = get_run_execute(supabase=ctx.services.supabase, run_id=run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run": run}


@router.get("/runs/{run_id}/history", summary="Get persisted LLM run history")
async def get_llm_run_history(run_id: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    from application.use_cases.get_run_history import execute as get_run_history_execute
    history = get_run_history_execute(supabase=ctx.services.supabase, run_id=run_id)
    if history.get("run") is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return history
