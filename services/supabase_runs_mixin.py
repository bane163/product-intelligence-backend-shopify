import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from objects.sanitize import sanitize_text

LOG = logging.getLogger(__name__)


class SupabaseRunsMixin:
    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_shop_domain(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        return normalized or None

    @staticmethod
    def _normalize_run_status(value: Any) -> str | None:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return None
        if normalized in {"queued", "pending", "created"}:
            return "queued"
        if normalized in {"running", "in_progress", "processing"}:
            return "running"
        if normalized in {"succeeded", "success", "completed", "complete", "done"}:
            return "succeeded"
        if normalized in {"failed", "error", "errored"}:
            return "failed"
        if normalized in {"cancelled", "canceled", "aborted"}:
            return "cancelled"
        return normalized

    def create_or_update_run(self, run_id: str, fields: dict[str, Any]) -> None:
        client = self._get_supabase_client()
        if not client:
            return
        payload = {"run_id": run_id, **fields}
        if "shop_domain" in payload:
            payload["shop_domain"] = self._normalize_shop_domain(payload.get("shop_domain"))
        if "status" in payload:
            payload["status"] = self._normalize_run_status(payload.get("status"))
        for key in ("prompt", "writer_prompt", "error"):
            if key in payload:
                payload[key] = sanitize_text(payload.get(key))
        try:
            client.table("llm_runs").upsert(payload, on_conflict="run_id").execute()
        except Exception:
            LOG.exception("Failed upserting llm_runs row for run_id=%s", run_id)

    def append_run_event(self, run_id: str, event: dict[str, Any], seq: int) -> None:
        client = self._get_supabase_client()
        if not client:
            return
        try:
            client.table("llm_run_events").insert(
                {
                    "run_id": run_id,
                    "ts": event.get("ts") or self._utc_now(),
                    "phase": event.get("phase", "unknown"),
                    "level": event.get("level", "info"),
                    "message": sanitize_text(event.get("message")) or "",
                    "payload_preview": sanitize_text(event.get("payload_preview")),
                    "error": sanitize_text(event.get("error")),
                    "seq": seq,
                }
            ).execute()
        except Exception:
            LOG.exception("Failed inserting llm_run_events row for run_id=%s", run_id)

    def append_run_message(
        self,
        run_id: str,
        *,
        role: str,
        message: Any,
        seq: int,
        meta: dict[str, Any] | None = None,
    ) -> None:
        client = self._get_supabase_client()
        if not client:
            return
        body = sanitize_text(message)
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
        self,
        run_id: str,
        *,
        status: str,
        duration_ms: int | None = None,
        error: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        normalized_status = self._normalize_run_status(status) or "failed"
        fields: dict[str, Any] = {
            "status": normalized_status,
            "ended_at": self._utc_now(),
            "duration_ms": duration_ms,
        }
        if error:
            fields["error"] = error
        if normalized_status == "failed":
            fields.setdefault("failure_code", "run_failed")
            fields.setdefault("failure_message", sanitize_text(error) if error else "Run failed")
            fields.setdefault("resume_token", str(uuid.uuid4()))
        elif normalized_status in {"succeeded", "cancelled"}:
            fields.setdefault("resume_token", None)
            fields.setdefault("failure_code", None)
            fields.setdefault("failure_message", None)
        if extra_fields:
            fields.update(extra_fields)
        self.create_or_update_run(run_id, fields)

    def list_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        client = self._get_supabase_client()
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
            normalized_shop_domain = self._normalize_shop_domain(shop_domain)
            if normalized_shop_domain:
                query = query.eq("shop_domain", normalized_shop_domain)
            res = query.execute()
            return res.data or []
        except Exception:
            LOG.exception("Failed listing llm_runs")
            return []

    def get_run(self, run_id: str, *, shop_domain: str | None = None) -> dict[str, Any] | None:
        client = self._get_supabase_client()
        if not client:
            return None
        try:
            query = client.table("llm_runs").select("*").eq("run_id", run_id)
            normalized_shop_domain = self._normalize_shop_domain(shop_domain)
            if normalized_shop_domain:
                query = query.eq("shop_domain", normalized_shop_domain)
            res = query.limit(1).execute()
            rows = res.data or []
            return rows[0] if rows else None
        except Exception:
            LOG.exception("Failed fetching llm_runs for run_id=%s", run_id)
            return None

    def get_run_history(self, run_id: str, *, shop_domain: str | None = None) -> dict[str, Any]:
        client = self._get_supabase_client()
        if not client:
            return {"run": None, "events": [], "messages": []}
        run = self.get_run(run_id, shop_domain=shop_domain)
        if not run:
            return {"run": None, "events": [], "messages": []}
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
