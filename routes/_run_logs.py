import asyncio
import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, AsyncIterator

MAX_EVENTS_PER_RUN = 300
MAX_PREVIEW_LENGTH = 1200

_run_history: dict[str, deque[dict[str, Any]]] = defaultdict(
    lambda: deque(maxlen=MAX_EVENTS_PER_RUN)
)
_run_subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
_run_done: set[str] = set()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_preview(value: Any) -> Any:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = (
        text.replace("SUPABASE_SERVICE_ROLE_KEY", "***")
        .replace("OPENAI_API_KEY", "***")
        .replace("OLLAMA_API_KEY", "***")
    )
    if len(text) > MAX_PREVIEW_LENGTH:
        return f"{text[:MAX_PREVIEW_LENGTH]}...(truncated)"
    return text


def emit_run_event(
    run_id: str,
    *,
    phase: str,
    message: str,
    level: str = "info",
    payload_preview: Any = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "run_id": run_id,
        "ts": _now_iso(),
        "phase": phase,
        "level": level,
        "message": message,
    }
    preview = _sanitize_preview(payload_preview)
    if preview is not None:
        event["payload_preview"] = preview
    if error:
        event["error"] = error
    if metadata:
        event["metadata"] = _sanitize_preview(metadata)

    _run_history[run_id].append(event)
    for queue in list(_run_subscribers.get(run_id, [])):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            continue
    return event


def complete_run(run_id: str) -> None:
    _run_done.add(run_id)


async def stream_run_events(run_id: str) -> AsyncIterator[str]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=MAX_EVENTS_PER_RUN)
    _run_subscribers[run_id].append(queue)

    try:
        yield "retry: 1000\n\n"
        for event in _run_history.get(run_id, []):
            yield f"data: {json.dumps(event)}\n\n"

        while True:
            if run_id in _run_done and queue.empty():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10)
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        subscribers = _run_subscribers.get(run_id)
        if subscribers and queue in subscribers:
            subscribers.remove(queue)
        if not subscribers:
            _run_subscribers.pop(run_id, None)
