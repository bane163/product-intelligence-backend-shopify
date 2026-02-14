import asyncio
import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from objects.sanitize import sanitize_preview


class TracingService:
    def __init__(self, max_events_per_run: int = 300):
        self.max_events_per_run = max_events_per_run
        self.run_history: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=self.max_events_per_run)
        )
        self.run_subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        self.run_done: set[str] = set()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def emit_run_event(
        self,
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
            "ts": self._now_iso(),
            "phase": phase,
            "level": level,
            "message": message,
        }
        preview = sanitize_preview(payload_preview)
        if preview is not None:
            event["payload_preview"] = preview
        if error:
            event["error"] = error
        if metadata:
            event["metadata"] = sanitize_preview(metadata)

        self.run_history[run_id].append(event)
        for queue in list(self.run_subscribers.get(run_id, [])):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                continue
        return event

    def complete_run(self, run_id: str) -> None:
        self.run_done.add(run_id)

    async def stream_run_events(self, run_id: str) -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.max_events_per_run)
        self.run_subscribers[run_id].append(queue)

        try:
            yield "retry: 1000\n\n"
            for event in self.run_history.get(run_id, []):
                yield f"data: {json.dumps(event)}\n\n"

            while True:
                if run_id in self.run_done and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=10)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            subscribers = self.run_subscribers.get(run_id)
            if subscribers and queue in subscribers:
                subscribers.remove(queue)
            if not subscribers:
                self.run_subscribers.pop(run_id, None)

