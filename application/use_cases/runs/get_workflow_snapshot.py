"""Use-case: read a workflow snapshot for realtime hydration/reconnect."""

from typing import Any

from application.ports.supabase_port import SupabaseNamespacedPort


def execute(
    supabase: SupabaseNamespacedPort,
    *,
    run_id: str,
    shop_domain: str,
    draft_id: str | None = None,
    after_seq: int = 0,
    event_limit: int = 200,
) -> dict[str, Any]:
    run = supabase.runs.get_run(run_id, shop_domain=shop_domain)
    if not run:
        return {"run": None, "draft": None, "events": []}

    history = supabase.runs.get_run_history(run_id, shop_domain=shop_domain)
    raw_events = history.get("events") if isinstance(history, dict) else []
    events = [item for item in raw_events if isinstance(item, dict)]

    next_events = [
        event
        for event in events
        if int(event.get("seq") or 0) > max(0, int(after_seq))
    ]
    next_events.sort(key=lambda event: int(event.get("seq") or 0))
    limited_events = next_events[: max(1, min(int(event_limit), 1000))]

    draft = None
    if isinstance(draft_id, str) and draft_id.strip():
        draft = supabase.drafts.get_product_draft(
            draft_id.strip(),
            shop_domain=shop_domain,
        )

    return {
        "run": run,
        "draft": draft,
        "events": limited_events,
    }
