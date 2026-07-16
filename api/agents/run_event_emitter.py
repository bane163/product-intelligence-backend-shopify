from typing import Any, Optional

from shared.observability import current_observability_fields


class RunEventEmitter:
    """Encapsulate per-run event and message sequencing and persistence.

    Usage:
        emitter = RunEventEmitter(tracing=tracing, supabase=supabase, run_id=run_id)
        emit_and_persist = emitter.emit_and_persist
        trace_event = emitter.trace_event
    """

    def __init__(self, tracing: Any, supabase: Any, run_id: str, initial_seq: int = 0):
        self.tracing = tracing
        self.supabase = supabase
        self.run_id = run_id
        latest_seq = getattr(supabase.runs, "get_latest_run_event_seq", lambda _run_id: 0)
        self.event_seq = initial_seq or latest_seq(run_id)
        self.message_seq = 0
        self.usage_totals = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def emit_and_persist(
        self,
        *,
        phase: str,
        message: str,
        level: str = "info",
        payload_preview: Any = None,
        error: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        """Create a run event via tracing service and persist it to Supabase.

        Mirrors the behavior previously implemented inline in handlers.
        """
        self.event_seq += 1
        event_metadata = dict(metadata or {})
        for key, value in current_observability_fields().items():
            event_metadata.setdefault(key, value)
        observability_run_fields = {
            key: value
            for key, value in event_metadata.items()
            if key in {"request_id", "correlation_id"} and isinstance(value, str)
        }
        if observability_run_fields:
            self.supabase.runs.create_or_update_run(self.run_id, observability_run_fields)
        event = self.tracing.emit_run_event(
            self.run_id,
            phase=phase,
            message=message,
            level=level,
            payload_preview=payload_preview,
            error=error,
            metadata=event_metadata or None,
        )
        self.supabase.runs.append_run_event(self.run_id, event, self.event_seq)
        return event

    def trace_event(self, **kwargs):
        """Convenience wrapper used as trace_event callback by downstream components."""
        event = self.emit_and_persist(
            phase=kwargs.get("phase", "trace"),
            message=kwargs.get("message", ""),
            level=kwargs.get("level", "info"),
            payload_preview=kwargs.get("payload_preview"),
            error=kwargs.get("error"),
            metadata=kwargs.get("metadata"),
        )

        transcript_text = kwargs.get("transcript_text")
        transcript_role = kwargs.get("transcript_role")
        if transcript_text and transcript_role:
            self.message_seq += 1
            self.supabase.runs.append_run_message(
                self.run_id,
                role=transcript_role,
                message=transcript_text,
                seq=self.message_seq,
                meta=kwargs.get("transcript_meta"),
            )

        metadata = kwargs.get("metadata") or {}
        usage = metadata.get("usage") if isinstance(metadata, dict) else None
        if isinstance(usage, dict):
            self.usage_totals["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            self.usage_totals["completion_tokens"] += int(
                usage.get("completion_tokens") or 0
            )
            self.usage_totals["total_tokens"] += int(usage.get("total_tokens") or 0)
            self.supabase.runs.create_or_update_run(
                self.run_id,
                {
                    "prompt_tokens": self.usage_totals["prompt_tokens"],
                    "completion_tokens": self.usage_totals["completion_tokens"],
                    "total_tokens": self.usage_totals["total_tokens"],
                },
            )

        if isinstance(metadata, dict) and metadata.get("model_name"):
            self.supabase.runs.create_or_update_run(
                self.run_id,
                {
                    "model_name": metadata.get("model_name"),
                    "provider": metadata.get("provider"),
                },
            )

        return event
