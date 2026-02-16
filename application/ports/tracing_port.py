from typing import Any, AsyncIterator, Protocol


class TracingPort(Protocol):
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
    ) -> dict[str, Any]: ...

    def complete_run(self, run_id: str) -> None: ...

    def stream_run_events(self, run_id: str) -> AsyncIterator[str]: ...
