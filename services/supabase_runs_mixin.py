import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from objects.sanitize import sanitize_text

LOG = logging.getLogger(__name__)


class SupabaseRunsMixin:
    TERMINAL_WORKFLOW_STATUSES = {"cancelled", "succeeded", "failed"}
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
    def _normalize_identifier(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
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

    @staticmethod
    def _is_missing_column_error(exc: Exception, *, column: str) -> bool:
        normalized = " ".join(
            str(exc)
            .lower()
            .replace('"', "")
            .replace("'", "")
            .replace("`", "")
            .split()
        )
        if column.lower() not in normalized:
            return False
        return (
            "could not find" in normalized
            or "does not exist" in normalized
            or "schema cache" in normalized
            or "missing" in normalized
        )

    @classmethod
    def _drop_missing_columns(
        cls, payload: dict[str, Any], exc: Exception, *, candidates: tuple[str, ...]
    ) -> dict[str, Any] | None:
        has_missing_candidate = any(
            cls._is_missing_column_error(exc, column=column) for column in candidates
        )
        if not has_missing_candidate:
            return None
        compat_payload = dict(payload)
        for column in candidates:
            compat_payload.pop(column, None)
        return compat_payload

    def create_or_update_run(self, run_id: str, fields: dict[str, Any]) -> None:
        client = self._get_supabase_client()
        payload = {"run_id": run_id, **fields}
        if "shop_domain" in payload:
            payload["shop_domain"] = self._normalize_shop_domain(
                payload.get("shop_domain")
            )
        if "status" in payload:
            payload["status"] = self._normalize_run_status(payload.get("status"))
        for key in ("request_id", "correlation_id"):
            if key in payload:
                payload[key] = self._normalize_identifier(payload.get(key))
        for key in ("prompt", "writer_prompt", "error"):
            if key in payload:
                payload[key] = sanitize_text(payload.get(key))
        memory_runs = getattr(self, "llm_runs", None)
        if isinstance(memory_runs, dict):
            memory_runs[run_id] = {**memory_runs.get(run_id, {}), **payload}
        if not client:
            return
        try:
            client.table("llm_runs").upsert(payload, on_conflict="run_id").execute()
        except Exception as exc:
            compat_payload = self._drop_missing_columns(
                payload,
                exc,
                candidates=("request_id", "correlation_id"),
            )
            if compat_payload is not None:
                try:
                    client.table("llm_runs").upsert(
                        compat_payload, on_conflict="run_id"
                    ).execute()
                    return
                except Exception:
                    LOG.exception(
                        "Retry without observability columns failed for llm_runs row run_id=%s",
                        run_id,
                    )
            LOG.exception("Failed upserting llm_runs row for run_id=%s", run_id)

    def append_run_event(self, run_id: str, event: dict[str, Any], seq: int) -> None:
        client = self._get_supabase_client()
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else None
        request_id = self._normalize_identifier(event.get("request_id"))
        correlation_id = self._normalize_identifier(event.get("correlation_id"))
        if metadata:
            request_id = request_id or self._normalize_identifier(metadata.get("request_id"))
            correlation_id = correlation_id or self._normalize_identifier(
                metadata.get("correlation_id")
            )
        memory_events = getattr(self, "llm_run_events", None)
        if isinstance(memory_events, dict):
            memory_events.setdefault(run_id, []).append(
                {
                    **event,
                    "run_id": run_id,
                    "message": sanitize_text(event.get("message")) or "",
                    "seq": seq,
                }
            )
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
                    "metadata": metadata or None,
                    "request_id": request_id,
                    "correlation_id": correlation_id,
                    "seq": seq,
                }
            ).execute()
        except Exception as exc:
            payload = {
                "run_id": run_id,
                "ts": event.get("ts") or self._utc_now(),
                "phase": event.get("phase", "unknown"),
                "level": event.get("level", "info"),
                "message": sanitize_text(event.get("message")) or "",
                "payload_preview": sanitize_text(event.get("payload_preview")),
                "error": sanitize_text(event.get("error")),
                "metadata": metadata or None,
                "request_id": request_id,
                "correlation_id": correlation_id,
                "seq": seq,
            }
            compat_payload = self._drop_missing_columns(
                payload,
                exc,
                candidates=("metadata", "request_id", "correlation_id"),
            )
            if compat_payload is not None:
                try:
                    client.table("llm_run_events").insert(compat_payload).execute()
                    return
                except Exception:
                    LOG.exception(
                        "Retry without observability columns failed for llm_run_events row run_id=%s",
                        run_id,
                    )
            LOG.exception("Failed inserting llm_run_events row for run_id=%s", run_id)

    def get_latest_run_event_seq(self, run_id: str) -> int:
        memory_events = getattr(self, "llm_run_events", None)
        latest = 0
        if isinstance(memory_events, dict):
            for item in memory_events.get(run_id, []):
                if isinstance(item, dict):
                    try:
                        latest = max(latest, int(item.get("seq") or 0))
                    except (TypeError, ValueError):
                        continue

        client = self._get_supabase_client()
        if not client:
            return latest
        try:
            result = (
                client.table("llm_run_events")
                .select("seq")
                .eq("run_id", run_id)
                .order("seq", desc=True)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            if rows:
                latest = max(latest, int(rows[0].get("seq") or 0))
        except Exception:
            LOG.exception("Failed resolving latest event sequence for run_id=%s", run_id)
        return latest

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
        payload["request_id"] = self._normalize_identifier(payload.get("request_id"))
        payload["correlation_id"] = self._normalize_identifier(
            payload.get("correlation_id")
        )
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
                compat_payload = self._drop_missing_columns(
                    payload,
                    exc,
                    candidates=("request_id", "correlation_id"),
                )
                if compat_payload is not None:
                    try:
                        client.table("offload_jobs").upsert(
                            compat_payload, on_conflict="job_id"
                        ).execute()
                        payload = compat_payload
                    except Exception as retry_exc:
                        LOG.exception(
                            "Retry without observability columns failed for offload_jobs row job_id=%s",
                            job_id,
                        )
                        if require_persistent_queue:
                            raise RuntimeError(
                                "Offload queue persistence failed (offload_jobs)"
                            ) from retry_exc
                else:
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
            is_queued_ready = status in {"queued", "retryable"} and available_at <= now
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
        for key in ("request_id", "correlation_id"):
            if key in payload:
                payload[key] = self._normalize_identifier(payload.get(key))
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
            except Exception as exc:
                compat_payload = self._drop_missing_columns(
                    payload,
                    exc,
                    candidates=("request_id", "correlation_id"),
                )
                if compat_payload is not None:
                    try:
                        res = (
                            client.table("offload_jobs")
                            .update(compat_payload)
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
                        payload = compat_payload
                    except Exception:
                        LOG.exception(
                            "Retry without observability columns failed for offload_jobs update job_id=%s",
                            job_id,
                        )
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

    def cancel_run_cascade(self, run_id: str, shop_domain: str | None) -> dict[str, Any] | None:
        """Atomically cancel a run, its runnable jobs, draft and control event."""
        tenant = self._normalize_shop_domain(shop_domain)
        client = self._get_supabase_client()
        if client:
            try:
                response = client.rpc(
                    "cancel_run_cascade",
                    {"p_run_id": run_id, "p_shop_domain": tenant},
                ).execute()
                rows = response.data or []
                return rows[0] if isinstance(rows, list) and rows else rows
            except Exception:
                LOG.exception("Failed cancelling workflow cascade run_id=%s", run_id)
                return None

        run = getattr(self, "llm_runs", {}).get(run_id)
        if not isinstance(run, dict):
            return None
        if tenant and self._normalize_shop_domain(run.get("shop_domain")) != tenant:
            return None
        if self._normalize_run_status(run.get("status")) not in {"queued", "running"}:
            return None
        now = self._utc_now()
        run.update(status="cancelled", ended_at=now, error=None,
                   failure_code="cancelled_by_operator", failure_message="Run cancelled",
                   resume_token=None)
        jobs = getattr(self, "offload_jobs", {})
        for job in jobs.values() if isinstance(jobs, dict) else ():
            if job.get("run_id") == run_id and self._normalize_offload_job_status(job.get("status")) not in self.TERMINAL_WORKFLOW_STATUSES:
                job.update(status="cancelled", error=None, worker_id=None,
                           claimed_at=None, claim_expires_at=None, updated_at=now)
        seq = self.get_latest_run_event_seq(run_id) + 1
        self.append_run_event(run_id, {"ts": now, "phase": "run_cancelled", "level": "info", "message": "Run cancelled", "metadata": {"operation": "cancel"}}, seq)
        return dict(run)

    def transition_offload_workflow(
        self, job_id: str, target_status: str, *, error: str | None = None,
        failure_code: str | None = None, result: dict[str, Any] | None = None,
        available_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Compare-and-set a job/run/draft workflow transition."""
        status = self._normalize_offload_job_status(target_status)
        client = self._get_supabase_client()
        if client:
            try:
                response = client.rpc("transition_offload_workflow", {
                    "p_job_id": job_id, "p_target_status": status,
                    "p_error": sanitize_text(error), "p_failure_code": failure_code,
                    "p_result": result, "p_available_at": available_at,
                }).execute()
                rows = response.data or []
                return rows[0] if isinstance(rows, list) and rows else rows
            except Exception:
                LOG.exception("Failed workflow transition job_id=%s status=%s", job_id, status)
                return None
        jobs = getattr(self, "offload_jobs", {})
        job = jobs.get(job_id) if isinstance(jobs, dict) else None
        if not isinstance(job, dict):
            return None
        current = self._normalize_offload_job_status(job.get("status"))
        if current in self.TERMINAL_WORKFLOW_STATUSES and current != status:
            return dict(job)
        now = self._utc_now()
        job.update(status=status, error=error, result=result, updated_at=now)
        if available_at is not None:
            job["available_at"] = available_at
        if status in {"retryable", "succeeded", "failed", "cancelled"}:
            job.update(worker_id=None, claimed_at=None, claim_expires_at=None)
        run = getattr(self, "llm_runs", {}).get(job.get("run_id"))
        mapped = "queued" if status == "retryable" else status
        if isinstance(run, dict) and self._normalize_run_status(run.get("status")) not in self.TERMINAL_WORKFLOW_STATUSES:
            run.update(status=mapped, error=error if mapped == "failed" else None)
            if mapped in self.TERMINAL_WORKFLOW_STATUSES:
                run["ended_at"] = now
            else:
                run.update(ended_at=None, duration_ms=None, failure_code=None, failure_message=None)
        return dict(job)

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

    def list_offload_jobs_for_run(
        self,
        run_id: str,
        *,
        shop_domain: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        normalized_limit = max(1, min(self._safe_int(limit, 20), 200))
        normalized_shop_domain = self._normalize_shop_domain(shop_domain)

        client = self._get_supabase_client()
        if client:
            try:
                query = (
                    client.table("offload_jobs")
                    .select("*")
                    .eq("run_id", run_id)
                    .order("created_at", desc=True)
                    .limit(normalized_limit)
                )
                if normalized_shop_domain:
                    query = query.eq("shop_domain", normalized_shop_domain)
                res = query.execute()
                return res.data or []
            except Exception:
                LOG.exception("Failed listing offload_jobs rows for run_id=%s", run_id)

        memory_jobs = getattr(self, "offload_jobs", None)
        if not isinstance(memory_jobs, dict):
            return []

        rows: list[dict[str, Any]] = []
        for item in memory_jobs.values():
            if not isinstance(item, dict):
                continue
            if str(item.get("run_id") or "") != run_id:
                continue
            if normalized_shop_domain:
                if self._normalize_shop_domain(item.get("shop_domain")) != normalized_shop_domain:
                    continue
            rows.append(item)
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return rows[:normalized_limit]

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
            run = getattr(self, "llm_runs", {}).get(run_id)
            normalized_shop_domain = self._normalize_shop_domain(shop_domain)
            if run and normalized_shop_domain and self._normalize_shop_domain(
                run.get("shop_domain")
            ) != normalized_shop_domain:
                return None
            return dict(run) if isinstance(run, dict) else None
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

    def get_run_summaries(
        self, run_ids: list[str], *, shop_domain: str | None = None
    ) -> dict[str, dict[str, Any]]:
        client = self._get_supabase_client()
        if not client:
            normalized_shop_domain = self._normalize_shop_domain(shop_domain)
            summaries: dict[str, dict[str, Any]] = {}
            memory_runs = getattr(self, "llm_runs", {})
            memory_events = getattr(self, "llm_run_events", {})
            for run_id in run_ids:
                run = memory_runs.get(run_id) if isinstance(memory_runs, dict) else None
                if not isinstance(run, dict):
                    continue
                if normalized_shop_domain and self._normalize_shop_domain(
                    run.get("shop_domain")
                ) != normalized_shop_domain:
                    continue
                summary = {
                    "status": self._normalize_run_status(run.get("status")),
                    "error": sanitize_text(run.get("error")),
                }
                events = memory_events.get(run_id, []) if isinstance(memory_events, dict) else []
                for event in reversed(events):
                    message = sanitize_text(event.get("message")) if isinstance(event, dict) else None
                    if message:
                        summary["processing_message"] = message
                        break
                summaries[run_id] = summary
            return summaries

        normalized_run_ids: list[str] = []
        seen_run_ids: set[str] = set()
        for run_id in run_ids:
            normalized_run_id = self._normalize_identifier(run_id)
            if not normalized_run_id or normalized_run_id in seen_run_ids:
                continue
            seen_run_ids.add(normalized_run_id)
            normalized_run_ids.append(normalized_run_id)

        if not normalized_run_ids:
            return {}

        normalized_shop_domain = self._normalize_shop_domain(shop_domain)
        summaries: dict[str, dict[str, Any]] = {}
        try:
            query = client.table("llm_runs").select("run_id,status,error")
            query = query.in_("run_id", normalized_run_ids)
            if normalized_shop_domain:
                query = query.eq("shop_domain", normalized_shop_domain)
            res = query.execute()
            for row in res.data or []:
                run_id = self._normalize_identifier(row.get("run_id"))
                if not run_id:
                    continue
                summaries[run_id] = {
                    "status": self._normalize_run_status(row.get("status")),
                    "error": sanitize_text(row.get("error")),
                }
        except Exception:
            LOG.exception(
                "Failed fetching batched llm_runs summaries for %s runs",
                len(normalized_run_ids),
            )
            return {}

        if not summaries:
            return {}

        try:
            event_limit = max(1000, len(summaries) * 20)
            res_events = (
                client.table("llm_run_events")
                .select("run_id,message,seq")
                .in_("run_id", list(summaries.keys()))
                .order("run_id")
                .order("seq", desc=True)
                .limit(event_limit)
                .execute()
            )
            for event in res_events.data or []:
                run_id = self._normalize_identifier(event.get("run_id"))
                if not run_id or run_id not in summaries:
                    continue
                if summaries[run_id].get("processing_message"):
                    continue
                message = sanitize_text(event.get("message"))
                if message:
                    summaries[run_id]["processing_message"] = message
        except Exception:
            LOG.exception(
                "Failed fetching batched llm_run_events summaries for %s runs",
                len(summaries),
            )

        return summaries

    def delete_run(self, run_id: str, *, shop_domain: str | None = None) -> bool:
        client = self._get_supabase_client()
        if not client:
            return False

        normalized_shop_domain = self._normalize_shop_domain(shop_domain)
        try:
            query = client.table("llm_runs").delete().eq("run_id", run_id)
            if normalized_shop_domain:
                query = query.eq("shop_domain", normalized_shop_domain)
            res = query.execute()
            deleted_rows = res.data or []
            if not deleted_rows:
                return False
        except Exception:
            LOG.exception("Failed deleting llm_runs row for run_id=%s", run_id)
            return False

        # Keep durable queue hygiene aligned with deleted run logs.
        try:
            cleanup_query = client.table("offload_jobs").delete().eq("run_id", run_id)
            if normalized_shop_domain:
                cleanup_query = cleanup_query.eq("shop_domain", normalized_shop_domain)
            cleanup_query.execute()
        except Exception:
            LOG.exception("Failed deleting offload_jobs rows for run_id=%s", run_id)

        return True

    def get_run_history(
        self, run_id: str, *, shop_domain: str | None = None
    ) -> dict[str, Any]:
        client = self._get_supabase_client()
        if not client:
            run = self.get_run(run_id, shop_domain=shop_domain)
            events = list(getattr(self, "llm_run_events", {}).get(run_id, []))
            return {"run": run, "events": events, "messages": []}
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
