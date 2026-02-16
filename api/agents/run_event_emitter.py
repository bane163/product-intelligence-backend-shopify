from typing import Any, Optional

from app_context import AppContext


class RunEventEmitter:
    """Encapsulate per-run event and message sequencing and persistence.

    Usage:
        emitter = RunEventEmitter(ctx=ctx, run_id=run_id)
        emit_and_persist = emitter.emit_and_persist
        trace_event = emitter.trace_event
    """

    def __init__(self, ctx: AppContext, run_id: str, initial_seq: int = 0):
        self.ctx = ctx
        self.run_id = run_id
        self.event_seq = initial_seq
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
        event = self.ctx.services.tracing.emit_run_event(
            self.run_id,
            phase=phase,
            message=message,
            level=level,
            payload_preview=payload_preview,
            error=error,
            metadata=metadata,
        )
        self.ctx.services.supabase.append_run_event(self.run_id, event, self.event_seq)
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
            self.ctx.services.supabase.append_run_message(
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
            self.ctx.services.supabase.create_or_update_run(
                self.run_id,
                {
                    "prompt_tokens": self.usage_totals["prompt_tokens"],
                    "completion_tokens": self.usage_totals["completion_tokens"],
                    "total_tokens": self.usage_totals["total_tokens"],
                },
            )

        if isinstance(metadata, dict) and metadata.get("model_name"):
            self.ctx.services.supabase.create_or_update_run(
                self.run_id,
                {
                    "model_name": metadata.get("model_name"),
                    "provider": metadata.get("provider"),
                },
            )

        return event
