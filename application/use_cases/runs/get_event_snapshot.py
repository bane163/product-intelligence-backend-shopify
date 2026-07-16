"""Read a tenant-scoped, merchant-safe incremental run event snapshot."""

from typing import Any

from application.ports.supabase_port import SupabaseNamespacedPort

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
TERMINAL_PHASES = {"request_done", "workflow_error", "workflow_cancelled"}


def _status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"completed", "complete", "success", "done"}:
        return "succeeded"
    if normalized in {"error", "errored"}:
        return "failed"
    if normalized in {"canceled", "aborted"}:
        return "cancelled"
    if normalized in {"pending", "created"}:
        return "queued"
    return normalized if normalized in {"queued", "running", *TERMINAL_STATUSES} else "running"


def _safe_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(event.get("run_id") or ""),
        "seq": int(event.get("seq") or 0),
        "ts": str(event.get("ts") or event.get("created_at") or ""),
        "phase": str(event.get("phase") or "processing"),
        "level": str(event.get("level") or "info"),
        "message": str(event.get("message") or "Processing update"),
        **({"error": "Processing failed"} if event.get("error") else {}),
    }


def execute(
    supabase: SupabaseNamespacedPort,
    *,
    run_id: str,
    shop_domain: str,
    after_seq: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    history = supabase.runs.get_run_history(run_id, shop_domain=shop_domain)
    run = history.get("run") if isinstance(history, dict) else None
    if not isinstance(run, dict):
        return {"run_id": run_id, "status": "unavailable", "terminal": True, "last_seq": 0, "events": []}

    safe_events = [
        _safe_event(item)
        for item in history.get("events", [])
        if isinstance(item, dict) and int(item.get("seq") or 0) > max(0, after_seq)
    ]
    safe_events.sort(key=lambda item: item["seq"])
    safe_events = safe_events[: max(1, min(limit, 200))]
    status = _status(run.get("status"))
    terminal = status in TERMINAL_STATUSES or any(
        event["phase"] in TERMINAL_PHASES for event in safe_events
    )
    last_seq = max(
        [int(item.get("seq") or 0) for item in history.get("events", []) if isinstance(item, dict)]
        or [max(0, after_seq)]
    )
    return {
        "run_id": run_id,
        "status": status,
        "terminal": terminal,
        "last_seq": last_seq,
        "events": safe_events,
        "retry_after_ms": None if terminal else 1000,
    }
