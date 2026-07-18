"""Use-case: fetch run diagnostics with history + offload/retry state."""

from typing import Any

from application.ports.supabase_port import SupabaseNamespacedPort


def _safe_limit(value: int, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def execute(
    supabase: SupabaseNamespacedPort,
    *,
    run_id: str,
    shop_domain: str,
    event_limit: int = 200,
    message_limit: int = 200,
    offload_limit: int = 20,
) -> dict[str, Any]:
    try:
        history = supabase.runs.get_run_history(
            run_id, shop_domain=shop_domain, include_messages=message_limit != 0
        )
    except TypeError as exc:
        if "include_messages" not in str(exc):
            raise
        history = supabase.runs.get_run_history(run_id, shop_domain=shop_domain)
    run = history.get("run") if isinstance(history, dict) else None
    if not isinstance(run, dict):
        return {
            "run": None,
            "history": {"events": [], "messages": []},
            "offload_jobs": [],
            "retry_diagnostics": {},
        }

    raw_events = history.get("events") if isinstance(history, dict) else []
    raw_messages = history.get("messages") if isinstance(history, dict) else []
    events = [event for event in raw_events if isinstance(event, dict)]
    messages = [message for message in raw_messages if isinstance(message, dict)]
    limited_events = events[: _safe_limit(event_limit, default=200, minimum=1, maximum=1000)]
    limited_messages = [] if message_limit == 0 else messages[
        : _safe_limit(message_limit, default=200, minimum=1, maximum=1000)
    ]

    jobs = supabase.runs.list_offload_jobs_for_run(
        run_id,
        shop_domain=shop_domain,
        limit=_safe_limit(offload_limit, default=20, minimum=1, maximum=200),
    )
    offload_jobs = [job for job in jobs if isinstance(job, dict)]
    retryable_jobs = [
        job
        for job in offload_jobs
        if str(job.get("status") or "").strip().lower() == "retryable"
    ]
    terminal_failed_jobs = [
        job
        for job in offload_jobs
        if str(job.get("status") or "").strip().lower() in {"failed", "cancelled"}
    ]

    retry_at_candidates = [
        str(job.get("available_at"))
        for job in retryable_jobs
        if isinstance(job.get("available_at"), str) and str(job.get("available_at")).strip()
    ]

    return {
        "run": run,
        "history": {"events": limited_events, "messages": limited_messages},
        "offload_jobs": offload_jobs,
        "retry_diagnostics": {
            "run_attempt": _safe_int(run.get("attempt"), 1),
            "run_status": run.get("status"),
            "failure_code": run.get("failure_code"),
            "failure_message": run.get("failure_message"),
            "last_completed_step": run.get("last_completed_step"),
            "resume_token_present": bool(str(run.get("resume_token") or "").strip()),
            "retryable_offload_jobs": len(retryable_jobs),
            "terminal_failed_offload_jobs": len(terminal_failed_jobs),
            "latest_retry_available_at": max(retry_at_candidates)
            if retry_at_candidates
            else None,
        },
    }
