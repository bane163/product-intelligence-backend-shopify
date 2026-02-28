import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from objects.sanitize import sanitize_text

LOG = logging.getLogger(__name__)


class SupabaseRunsMixin:
    def _get_supabase_client(self) -> Optional[Any]:
        """Stub for typing — actual implementation provided by host class (e.g. SupabaseFileMixin)."""
        raise NotImplementedError(
            "_get_supabase_client must be implemented by the host class"
        )

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

    @staticmethod
    def _normalize_queue_name(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        return normalized or "default"

    @staticmethod
    def _normalize_offload_job_status(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return "queued"
        if normalized in {"pending", "created"}:
            return "queued"
        if normalized in {"in_progress", "processing"}:
            return "running"
        if normalized in {"success", "completed", "complete", "done"}:
            return "succeeded"
        if normalized in {"error", "errored"}:
            return "failed"
        if normalized in {"canceled", "aborted"}:
            return "cancelled"
        if normalized in {"retry", "retrying"}:
            return "retryable"
        return normalized

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def create_or_update_run(self, run_id: str, fields: dict[str, Any]) -> None:
        client = self._get_supabase_client()
        if not client:
            return
        payload = {"run_id": run_id, **fields}
        if "shop_domain" in payload:
            payload["shop_domain"] = self._normalize_shop_domain(
                payload.get("shop_domain")
            )
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
            fields.setdefault(
                "failure_message", sanitize_text(error) if error else "Run failed"
            )
            fields.setdefault("resume_token", str(uuid.uuid4()))
        elif normalized_status in {"succeeded", "cancelled"}:
            fields.setdefault("resume_token", None)
            fields.setdefault("failure_code", None)
            fields.setdefault("failure_message", None)
        if extra_fields:
            fields.update(extra_fields)
        self.create_or_update_run(run_id, fields)

    def enqueue_offload_job(
        self,
        job_id: str,
        fields: dict[str, Any],
        *,
        require_persistent_queue: bool = False,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {"job_id": job_id, **fields}
        payload["queue_name"] = self._normalize_queue_name(payload.get("queue_name"))
        payload["job_type"] = str(payload.get("job_type") or "unknown").strip().lower()
        payload["status"] = self._normalize_offload_job_status(payload.get("status"))
        payload["priority"] = max(0, self._safe_int(payload.get("priority"), 100))
        payload["attempt_count"] = max(
            0, self._safe_int(payload.get("attempt_count"), 0)
        )
        payload["max_attempts"] = max(1, self._safe_int(payload.get("max_attempts"), 5))
        payload["shop_domain"] = self._normalize_shop_domain(payload.get("shop_domain"))
        payload["available_at"] = payload.get("available_at") or self._utc_now()
        payload["created_at"] = payload.get("created_at") or self._utc_now()
        payload["updated_at"] = self._utc_now()

        client = self._get_supabase_client()
        if client:
            try:
                client.table("offload_jobs").upsert(
                    payload, on_conflict="job_id"
                ).execute()
            except Exception as exc:
                LOG.exception("Failed upserting offload_jobs row for job_id=%s", job_id)
                if require_persistent_queue:
                    raise RuntimeError(
                        "Offload queue persistence failed (offload_jobs)"
                    ) from exc

        memory_jobs = getattr(self, "offload_jobs", None)
        if isinstance(memory_jobs, dict):
            existing = memory_jobs.get(job_id) or {}
            merged = {**existing, **payload}
            memory_jobs[job_id] = merged
            return merged
        return payload

    def claim_next_offload_job(
        self,
        *,
        queue_name: str = "default",
        worker_id: str,
        lease_seconds: int = 300,
    ) -> dict[str, Any] | None:
        normalized_queue = self._normalize_queue_name(queue_name)
        lease = max(1, self._safe_int(lease_seconds, 300))
        normalized_worker = str(worker_id or "").strip() or None

        client = self._get_supabase_client()
        if client:
            try:
                res = client.rpc(
                    "claim_next_offload_job",
                    {
                        "p_queue_name": normalized_queue,
                        "p_worker_id": normalized_worker,
                        "p_lease_seconds": lease,
                    },
                ).execute()
                rows = res.data or []
                if rows:
                    claimed = rows[0]
                    job_id = claimed.get("job_id")
                    memory_jobs = getattr(self, "offload_jobs", None)
                    if isinstance(memory_jobs, dict) and isinstance(job_id, str):
                        memory_jobs[job_id] = claimed
                    return claimed
                return None
            except Exception:
                LOG.exception(
                    "Failed claiming offload_jobs row for queue_name=%s",
                    normalized_queue,
                )

        memory_jobs = getattr(self, "offload_jobs", None)
        if not isinstance(memory_jobs, dict):
            return None

        def parse_ts(value: Any) -> datetime | None:
            if not isinstance(value, str) or not value:
                return None
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None

        now = datetime.now(timezone.utc)
        candidates: list[dict[str, Any]] = []
        for item in memory_jobs.values():
            if self._normalize_queue_name(item.get("queue_name")) != normalized_queue:
                continue
            attempt_count = max(0, self._safe_int(item.get("attempt_count"), 0))
            max_attempts = max(1, self._safe_int(item.get("max_attempts"), 5))
            if attempt_count >= max_attempts:
                continue
            status = self._normalize_offload_job_status(item.get("status"))
            available_at = parse_ts(item.get("available_at")) or now
            claim_expires_at = parse_ts(item.get("claim_expires_at"))
            is_queued_ready = status == "queued" and available_at <= now
            is_claim_expired = (
                status == "claimed"
                and claim_expires_at is not None
                and claim_expires_at <= now
            )
            if is_queued_ready or is_claim_expired:
                candidates.append(item)

        if not candidates:
            return None

        candidates.sort(
            key=lambda item: (
                max(0, self._safe_int(item.get("priority"), 100)),
                str(item.get("available_at") or ""),
                str(item.get("created_at") or ""),
            )
        )
        claimed_job = dict(candidates[0])
        claimed_job["status"] = "claimed"
        claimed_job["worker_id"] = normalized_worker
        claimed_job["claimed_at"] = now.isoformat()
        claimed_job["claim_expires_at"] = (now + timedelta(seconds=lease)).isoformat()
        claimed_job["attempt_count"] = (
            max(0, self._safe_int(claimed_job.get("attempt_count"), 0)) + 1
        )
        claimed_job["updated_at"] = now.isoformat()
        job_id = claimed_job.get("job_id")
        if isinstance(job_id, str):
            memory_jobs[job_id] = claimed_job
        return claimed_job

    def update_offload_job(
        self, job_id: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        payload = dict(fields)
        if "queue_name" in payload:
            payload["queue_name"] = self._normalize_queue_name(
                payload.get("queue_name")
            )
        if "status" in payload:
            payload["status"] = self._normalize_offload_job_status(
                payload.get("status")
            )
        if "shop_domain" in payload:
            payload["shop_domain"] = self._normalize_shop_domain(
                payload.get("shop_domain")
            )
        for int_key, default in (
            ("priority", 100),
            ("attempt_count", 0),
            ("max_attempts", 5),
        ):
            if int_key in payload:
                min_value = 1 if int_key == "max_attempts" else 0
                payload[int_key] = max(
                    min_value, self._safe_int(payload.get(int_key), default)
                )
        if "error" in payload:
            payload["error"] = sanitize_text(payload.get("error"))
        payload["updated_at"] = self._utc_now()

        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("offload_jobs")
                    .update(payload)
                    .eq("job_id", job_id)
                    .execute()
                )
                rows = res.data or []
                if rows:
                    row = rows[0]
                    memory_jobs = getattr(self, "offload_jobs", None)
                    if isinstance(memory_jobs, dict):
                        memory_jobs[job_id] = row
                    return row
            except Exception:
                LOG.exception("Failed updating offload_jobs row for job_id=%s", job_id)

        memory_jobs = getattr(self, "offload_jobs", None)
        if not isinstance(memory_jobs, dict):
            return None
        existing = memory_jobs.get(job_id)
        if not isinstance(existing, dict):
            return None
        merged = {**existing, **payload}
        memory_jobs[job_id] = merged
        return merged

    def get_offload_job(self, job_id: str) -> dict[str, Any] | None:
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("offload_jobs")
                    .select("*")
                    .eq("job_id", job_id)
                    .limit(1)
                    .execute()
                )
                rows = res.data or []
                if rows:
                    row = rows[0]
                    memory_jobs = getattr(self, "offload_jobs", None)
                    if isinstance(memory_jobs, dict):
                        memory_jobs[job_id] = row
                    return row
            except Exception:
                LOG.exception("Failed fetching offload_jobs row for job_id=%s", job_id)

        memory_jobs = getattr(self, "offload_jobs", None)
        if isinstance(memory_jobs, dict):
            return memory_jobs.get(job_id)
        return None

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

    def get_run(
        self, run_id: str, *, shop_domain: str | None = None
    ) -> dict[str, Any] | None:
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

    def get_run_history(
        self, run_id: str, *, shop_domain: str | None = None
    ) -> dict[str, Any]:
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
