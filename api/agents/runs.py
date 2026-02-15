"""Run tracing endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app_context import AppContext, get_ctx

router = APIRouter()


@router.get("/runs/{run_id}/events", summary="Stream live workflow events")
async def stream_events(run_id: str, ctx: AppContext = Depends(get_ctx)) -> StreamingResponse:
    return StreamingResponse(
        ctx.services.tracing.stream_run_events(run_id),
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
    return {"runs": ctx.services.supabase.list_runs(limit=limit, offset=offset, status=status)}


@router.get("/runs/{run_id}", summary="Get persisted LLM run")
async def get_llm_run(run_id: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, dict]:
    run = ctx.services.supabase.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run": run}


@router.get("/runs/{run_id}/history", summary="Get persisted LLM run history")
async def get_llm_run_history(run_id: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    history = ctx.services.supabase.get_run_history(run_id)
    if history.get("run") is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return history
