import json
import logging
from datetime import datetime, timezone
from typing import Any

LOG = logging.getLogger(__name__)
MAX_TEXT_LENGTH = 12000


def _sanitize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = (
        text.replace("SUPABASE_SERVICE_ROLE_KEY", "***")
        .replace("OPENAI_API_KEY", "***")
        .replace("OLLAMA_API_KEY", "***")
    )
    if len(text) > MAX_TEXT_LENGTH:
        return f"{text[:MAX_TEXT_LENGTH]}...(truncated)"
    return text


def _get_supabase_client():
    try:
        from ..supabase_client import get_supabase
        return get_supabase()
    except ImportError:
        try:
            from supabase_client import get_supabase
            return get_supabase()
        except Exception:
            return None
    except Exception:
        LOG.debug("Supabase client unavailable", exc_info=True)
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_or_update_run(run_id: str, fields: dict[str, Any]) -> None:
    client = _get_supabase_client()
    if not client:
        return
    payload = {"run_id": run_id, **fields}
    for key in ("prompt", "writer_prompt", "error"):
        if key in payload:
            payload[key] = _sanitize_text(payload.get(key))
    try:
        client.table("llm_runs").upsert(payload, on_conflict="run_id").execute()
    except Exception:
        LOG.exception("Failed upserting llm_runs row for run_id=%s", run_id)


def append_run_event(run_id: str, event: dict[str, Any], seq: int) -> None:
    client = _get_supabase_client()
    if not client:
        return
    try:
        client.table("llm_run_events").insert(
            {
                "run_id": run_id,
                "ts": event.get("ts") or _utc_now(),
                "phase": event.get("phase", "unknown"),
                "level": event.get("level", "info"),
                "message": _sanitize_text(event.get("message")) or "",
                "payload_preview": _sanitize_text(event.get("payload_preview")),
                "error": _sanitize_text(event.get("error")),
                "seq": seq,
            }
        ).execute()
    except Exception:
        LOG.exception("Failed inserting llm_run_events row for run_id=%s", run_id)


def append_run_message(
    run_id: str,
    *,
    role: str,
    message: Any,
    seq: int,
    meta: dict[str, Any] | None = None,
) -> None:
    client = _get_supabase_client()
    if not client:
        return
    body = _sanitize_text(message)
    if not body:
        return
    try:
        client.table("llm_run_messages").insert(
            {
                "run_id": run_id,
                "role": role,
                "message": body,
                "meta": meta or {},
                "seq": seq,
            }
        ).execute()
    except Exception:
        LOG.exception("Failed inserting llm_run_messages row for run_id=%s", run_id)


def finalize_run(
    run_id: str,
    *,
    status: str,
    duration_ms: int | None = None,
    error: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> None:
    fields: dict[str, Any] = {
        "status": status,
        "ended_at": _utc_now(),
        "duration_ms": duration_ms,
    }
    if error:
        fields["error"] = error
    if extra_fields:
        fields.update(extra_fields)
    create_or_update_run(run_id, fields)


def list_runs(limit: int = 50, offset: int = 0, status: str | None = None) -> list[dict[str, Any]]:
    client = _get_supabase_client()
    if not client:
        return []
    try:
        query = (
            client.table("llm_runs")
            .select("*")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        if status:
            query = query.eq("status", status)
        res = query.execute()
        return res.data or []
    except Exception:
        LOG.exception("Failed listing llm_runs")
        return []


def get_run(run_id: str) -> dict[str, Any] | None:
    client = _get_supabase_client()
    if not client:
        return None
    try:
        res = client.table("llm_runs").select("*").eq("run_id", run_id).limit(1).execute()
        rows = res.data or []
        return rows[0] if rows else None
    except Exception:
        LOG.exception("Failed fetching llm_runs for run_id=%s", run_id)
        return None


def get_run_history(run_id: str) -> dict[str, Any]:
    client = _get_supabase_client()
    if not client:
        return {"run": None, "events": [], "messages": []}
    run = get_run(run_id)
    events: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    try:
        res_events = (
            client.table("llm_run_events")
            .select("*")
            .eq("run_id", run_id)
            .order("seq")
            .limit(1000)
            .execute()
        )
        events = res_events.data or []
    except Exception:
        LOG.exception("Failed fetching llm_run_events for run_id=%s", run_id)

    try:
        res_messages = (
            client.table("llm_run_messages")
            .select("*")
            .eq("run_id", run_id)
            .order("seq")
            .limit(1000)
            .execute()
        )
        messages = res_messages.data or []
    except Exception:
        LOG.exception("Failed fetching llm_run_messages for run_id=%s", run_id)

    return {"run": run, "events": events, "messages": messages}
