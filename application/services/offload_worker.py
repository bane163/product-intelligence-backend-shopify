import asyncio
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app_context import AppContext, get_app_context
from application.use_cases.drafts.get_product_draft import execute as get_draft_execute
from application.use_cases.drafts.save_product_draft import execute as save_draft_execute
from application.use_cases.files.get_file import execute as get_file_execute
from application.use_cases.processing.process_document import (
    execute as process_document_execute,
)
from application.use_cases.processing.submit_products import execute as submit_execute
from shared.metrics_signals import signal_offload_queue, signal_worker_job_failure
from shared.observability import bind_observability_context, current_observability_fields

LOG = logging.getLogger(__name__)


def _optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_str_field(data: dict[str, Any] | None, key: str) -> str | None:
    if not isinstance(data, dict):
        return None
    return _optional_str(data.get(key))


def _draft_products(entry: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(entry, dict):
        return []
    raw_products = entry.get("products")
    if not isinstance(raw_products, list):
        return []
    return [item for item in raw_products if isinstance(item, dict)]


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _extract_products_from_result_payload(
    payload: Any,
) -> list[dict[str, Any]] | None:
    raw_products: Any = None
    if isinstance(payload, dict):
        raw_products = payload.get("products")
    else:
        model_dump = getattr(payload, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump(mode="json")
            if isinstance(dumped, dict):
                raw_products = dumped.get("products")
    if not isinstance(raw_products, list):
        return None
    return [item for item in raw_products if isinstance(item, dict)]


def _safe_int(value: Any, default: int, *, minimum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return parsed if parsed >= minimum else minimum


def _safe_float(value: Any, default: float, *, minimum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return parsed if parsed >= minimum else minimum


def _default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def _compute_retry_delay_seconds(attempt_count: int) -> int:
    normalized_attempt = max(1, int(attempt_count))
    # Exponential backoff (15s, 30s, 60s, ...) capped at 5 minutes.
    return min(300, 15 * (2 ** (normalized_attempt - 1)))


def _is_retryable_job_failure(*, job_type: str, error_message: str) -> bool:
    if job_type != "document_import":
        return False
    lowered = error_message.lower()
    non_retryable_markers = (
        "file not found",
        "missing file_id",
        "content is invalid",
        "file content is invalid",
        "file content does not match",
        "file is not a zip file",
        "lifecycle columns missing",
        "collabora_storage_low",
        "out of storage",
        "low disk",
    )
    return not any(marker in lowered for marker in non_retryable_markers)


def _exception_message(exc: Exception) -> str:
    return str(exc).strip() or type(exc).__name__


def _failure_code(error_message: str) -> str:
    lowered = error_message.lower()
    if "storage" in lowered or "low disk" in lowered:
        return "collabora_storage_low"
    if "timeout" in lowered or "readtimeout" in lowered:
        return "collabora_timeout"
    if "collabora" in lowered or "connection" in lowered:
        return "collabora_unavailable"
    return "workflow_failed"


def _estimate_queue_backlog(ctx: AppContext, queue_name: str) -> int | None:
    service = getattr(ctx.supabase, "_service", None)
    memory_jobs = getattr(service, "offload_jobs", None)
    if not isinstance(memory_jobs, dict):
        return None

    normalized_queue = (_optional_str(queue_name) or "offload").lower()
    backlog = 0
    for value in memory_jobs.values():
        if not isinstance(value, dict):
            continue
        if (_optional_str(value.get("queue_name")) or "default").lower() != normalized_queue:
            continue
        status = (_optional_str(value.get("status")) or "").lower()
        if status in {"queued", "retryable"}:
            backlog += 1
    return backlog


def _save_draft_state(
    *,
    ctx: AppContext,
    draft_id: str,
    shop_domain: str | None = None,
    fallback_run_id: str | None,
    fallback_import_mode: str = "auto",
    fallback_name: str | None = None,
    fallback_input_file_id: str | None = None,
    fallback_input_filename: str | None = None,
    products: list[dict[str, Any]] | None = None,
    output_file_id: str | None = None,
    output_filename: str | None = None,
    extraction_status: str | None = None,
    extraction_run_id: str | None = None,
    extraction_error: str | None = None,
    submit_status: str | None = None,
    submit_run_id: str | None = None,
    submit_error: str | None = None,
) -> None:
    existing = (
        get_draft_execute(
            supabase=ctx.supabase,
            draft_id=draft_id,
            shop_domain=shop_domain,
        )
        or {}
    )
    save_draft_execute(
        supabase=ctx.supabase,
        draft_id=draft_id,
        run_id=_optional_str_field(existing, "run_id") or fallback_run_id,
        import_mode=_optional_str_field(existing, "import_mode") or fallback_import_mode,
        draft_name=_optional_str_field(existing, "draft_name") or fallback_name,
        shop_domain=_optional_str_field(existing, "shop_domain") or shop_domain,
        input_file_id=_optional_str_field(existing, "input_file_id") or fallback_input_file_id,
        input_filename=_optional_str_field(existing, "input_filename")
        or fallback_input_filename,
        output_file_id=output_file_id
        if output_file_id is not None
        else _optional_str_field(existing, "output_file_id"),
        output_filename=output_filename
        if output_filename is not None
        else _optional_str_field(existing, "output_filename"),
        extraction_status=extraction_status
        if extraction_status is not None
        else _optional_str_field(existing, "extraction_status"),
        extraction_run_id=extraction_run_id
        if extraction_run_id is not None
        else _optional_str_field(existing, "extraction_run_id"),
        extraction_error=extraction_error
        if extraction_error is not None
        else _optional_str_field(existing, "extraction_error"),
        submit_status=submit_status
        if submit_status is not None
        else _optional_str_field(existing, "submit_status"),
        submit_run_id=submit_run_id
        if submit_run_id is not None
        else _optional_str_field(existing, "submit_run_id"),
        submit_error=submit_error
        if submit_error is not None
        else _optional_str_field(existing, "submit_error"),
        require_lifecycle_columns=True,
        products=products if products is not None else _draft_products(existing),
    )


class OffloadWorker:
    def __init__(
        self,
        *,
        ctx: AppContext | None = None,
        queue_name: str = "offload",
        worker_id: str | None = None,
        lease_seconds: int = 300,
        poll_seconds: float = 2.0,
    ) -> None:
        self.ctx = ctx or get_app_context()
        self.queue_name = _optional_str(queue_name) or "offload"
        self.worker_id = _optional_str(worker_id) or _default_worker_id()
        self.lease_seconds = _safe_int(lease_seconds, 300, minimum=1)
        self.poll_seconds = _safe_float(poll_seconds, 2.0, minimum=0.1)

    @classmethod
    def from_env(cls, *, ctx: AppContext | None = None) -> "OffloadWorker":
        return cls(
            ctx=ctx,
            queue_name=os.getenv("OFFLOAD_QUEUE_NAME", "offload"),
            worker_id=os.getenv("OFFLOAD_WORKER_ID"),
            lease_seconds=_safe_int(
                os.getenv("OFFLOAD_LEASE_SECONDS"), 300, minimum=1
            ),
            poll_seconds=_safe_float(
                os.getenv("OFFLOAD_POLL_SECONDS"), 2.0, minimum=0.1
            ),
        )

    async def _process_document_import(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        run_id = _optional_str(job.get("run_id"))
        draft_id = _optional_str(job.get("draft_id"))
        file_id = _optional_str(job.get("file_id"))
        shop_domain = _optional_str(job.get("shop_domain"))
        input_name = _optional_str(payload.get("input_filename"))
        input_content_type = _optional_str(payload.get("input_content_type"))
        extraction_mode = _optional_str(payload.get("extraction_mode")) or "per_sheet"
        write_to_file = _parse_bool(payload.get("write_to_file"))
        output_path = _optional_str(payload.get("output_path"))
        collabora_url = _optional_str(payload.get("collabora_url"))
        import_mode = _optional_str(payload.get("import_mode")) or "auto"
        auto_submit = _parse_bool(payload.get("auto_submit"))
        requested_submit_run_id = _optional_str(payload.get("submit_run_id"))

        if not file_id:
            raise RuntimeError("Document import offload job missing file_id")

        file_entry = get_file_execute(supabase=self.ctx.supabase, file_id=file_id)
        if not isinstance(file_entry, dict):
            raise RuntimeError("Document import file not found")
        content = file_entry.get("content")
        if not isinstance(content, (bytes, bytearray)):
            raise RuntimeError("Document import file content is invalid")
        file_bytes = bytes(content)
        input_name = input_name or _optional_str(file_entry.get("name"))
        input_content_type = input_content_type or _optional_str(
            file_entry.get("content_type")
        )

        if draft_id:
            _save_draft_state(
                ctx=self.ctx,
                draft_id=draft_id,
                shop_domain=shop_domain,
                fallback_run_id=run_id,
                fallback_name=input_name,
                fallback_input_file_id=file_id,
                fallback_input_filename=input_name,
                extraction_status="running",
                extraction_run_id=run_id,
                extraction_error=None,
            )
        if run_id:
            self.ctx.supabase.runs.create_or_update_run(
                run_id,
                {
                    "status": "running",
                    "shop_domain": shop_domain,
                    **current_observability_fields(),
                },
            )

        result = await process_document_execute(
            supabase=self.ctx.supabase,
            llm=self.ctx.services.llm,
            tracing=self.ctx.services.tracing,
            ctx=self.ctx,
            file_bytes=file_bytes,
            input_name=input_name,
            input_content_type=input_content_type,
            run_id=run_id,
            collabora_url=collabora_url,
            extraction_mode=extraction_mode,
            write_to_file=write_to_file,
            output_path=output_path,
            shop_domain=shop_domain,
            manage_lifecycle=False,
            source_file_id=file_id,
        )
        result_payload = result.get("result") if isinstance(result, dict) else None

        output_file_id = None
        output_filename = None
        extracted_products: list[dict[str, Any]] | None = None
        if isinstance(result_payload, dict):
            output_file_id = _optional_str(result_payload.get("file_id"))
            output_filename = _optional_str(result_payload.get("filename"))
        extracted_products = _extract_products_from_result_payload(result_payload)

        if draft_id:
            _save_draft_state(
                ctx=self.ctx,
                draft_id=draft_id,
                shop_domain=shop_domain,
                fallback_run_id=run_id,
                fallback_name=input_name,
                fallback_input_file_id=file_id,
                fallback_input_filename=input_name,
                products=extracted_products,
                output_file_id=output_file_id,
                output_filename=output_filename,
                extraction_status="succeeded",
                extraction_run_id=run_id,
                extraction_error=None,
            )

            draft_state = get_draft_execute(
                supabase=self.ctx.supabase,
                draft_id=draft_id,
                shop_domain=shop_domain,
            )
            draft_submit_status = (
                (_optional_str_field(draft_state, "submit_status") or "").lower()
                if isinstance(draft_state, dict)
                else ""
            )
            draft_submit_run_id = (
                _optional_str_field(draft_state, "submit_run_id")
                if isinstance(draft_state, dict)
                else None
            )
            should_queue_submit = auto_submit or bool(draft_submit_run_id)
            if should_queue_submit and draft_submit_status not in {
                "queued",
                "running",
                "succeeded",
            }:
                submit_run_id = (
                    requested_submit_run_id or draft_submit_run_id or str(uuid.uuid4())
                )
                document_name = (
                    _optional_str_field(draft_state, "draft_name")
                    if isinstance(draft_state, dict)
                    else None
                ) or input_name
                try:
                    _save_draft_state(
                        ctx=self.ctx,
                        draft_id=draft_id,
                        shop_domain=shop_domain,
                        fallback_run_id=run_id,
                        fallback_import_mode=import_mode,
                        fallback_name=document_name,
                        submit_status="queued",
                        submit_run_id=submit_run_id,
                        submit_error=None,
                    )
                    self.ctx.supabase.runs.create_or_update_run(
                        submit_run_id,
                        {
                            "status": "queued",
                            "source": "shopify_submit",
                            "started_at": datetime.now(timezone.utc).isoformat(),
                            "attempt": 1,
                            "shop_domain": shop_domain,
                        },
                    )
                    self.ctx.supabase.runs.enqueue_offload_job(
                        str(uuid.uuid4()),
                        {
                            "queue_name": "offload",
                            "job_type": "shopify_submit",
                            "status": "queued",
                            "run_id": submit_run_id,
                            "draft_id": draft_id,
                            "submitted_id": None,
                            "shop_domain": shop_domain,
                            "payload": {
                                "import_mode": import_mode,
                                "document_name": document_name,
                                "products_json": None,
                                "shop_access_token": None,
                                "has_shop_access_token": False,
                            },
                        },
                        require_persistent_queue=True,
                    )
                except Exception as exc:
                    _save_draft_state(
                        ctx=self.ctx,
                        draft_id=draft_id,
                        shop_domain=shop_domain,
                        fallback_run_id=run_id,
                        fallback_import_mode=import_mode,
                        fallback_name=document_name,
                        submit_status="failed",
                        submit_run_id=submit_run_id,
                        submit_error=str(exc),
                    )
                    LOG.exception(
                        "Failed queueing auto submit for draft_id=%s run_id=%s",
                        draft_id,
                        run_id,
                    )

        return {
            "run_id": run_id,
            "draft_id": draft_id,
            "file_id": file_id,
            "output_file_id": output_file_id,
            "output_filename": output_filename,
            "product_count": len(extracted_products or []),
        }

    async def _process_shopify_submit(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        run_id = _optional_str(job.get("run_id"))
        draft_id = _optional_str(job.get("draft_id"))
        submitted_id = _optional_str(job.get("submitted_id"))
        shop_domain = _optional_str(job.get("shop_domain"))
        import_mode = _optional_str(payload.get("import_mode")) or "auto"
        document_name = _optional_str(payload.get("document_name"))
        products_json = _optional_str(payload.get("products_json"))
        shop_access_token = _optional_str(payload.get("shop_access_token"))
        LOG.info(
            "offload_submit_start run_id=%s draft_id=%s submitted_id=%s shop_domain=%s "
            "import_mode=%s has_products_json=%s has_shop_access_token=%s",
            run_id,
            draft_id,
            submitted_id,
            shop_domain,
            import_mode,
            bool(products_json),
            bool(shop_access_token),
        )

        if draft_id:
            _save_draft_state(
                ctx=self.ctx,
                draft_id=draft_id,
                shop_domain=shop_domain,
                fallback_run_id=run_id,
                fallback_name=document_name,
                submit_status="running",
                submit_run_id=run_id,
                submit_error=None,
            )
        if run_id:
            self.ctx.supabase.runs.create_or_update_run(
                run_id,
                {
                    "status": "running",
                    "shop_domain": shop_domain,
                },
            )

        result = await submit_execute(
            supabase=self.ctx.supabase,
            shopify=self.ctx.services.shopify,
            tracing=self.ctx.services.tracing,
            products_json=products_json,
            import_mode=import_mode,
            run_id=run_id,
            draft_id=draft_id,
            submitted_id=submitted_id,
            document_name=document_name,
            shop_domain=shop_domain,
            shop_access_token=shop_access_token,
        )
        LOG.info(
            "offload_submit_result run_id=%s draft_id=%s submitted_id=%s success_count=%s failed_count=%s error=%s",
            run_id,
            draft_id,
            result.get("submitted_id") if isinstance(result, dict) else None,
            result.get("success_count") if isinstance(result, dict) else None,
            result.get("failed_count") if isinstance(result, dict) else None,
            result.get("error") if isinstance(result, dict) else None,
        )
        submit_succeeded = bool(
            isinstance(result, dict)
            and isinstance(result.get("submitted_id"), str)
            and result.get("submitted_id")
        )
        error_detail: str | None = None
        if not submit_succeeded and isinstance(result, dict):
            raw_error = result.get("error")
            if isinstance(raw_error, str) and raw_error.strip():
                error_detail = raw_error.strip()
            else:
                success_count = result.get("success_count")
                failed_count = result.get("failed_count")
                if isinstance(success_count, int) and isinstance(failed_count, int):
                    error_detail = (
                        "Submit completed with no successful products "
                        f"(success_count={success_count}, failed_count={failed_count})"
                    )
                elif isinstance(failed_count, int):
                    error_detail = (
                        "Submit completed with no successful products "
                        f"(failed_count={failed_count})"
                    )
        if draft_id:
            _save_draft_state(
                ctx=self.ctx,
                draft_id=draft_id,
                shop_domain=shop_domain,
                fallback_run_id=run_id,
                fallback_name=document_name,
                submit_status="succeeded" if submit_succeeded else "failed",
                submit_run_id=run_id,
                submit_error=None if submit_succeeded else (error_detail or "Submit did not complete"),
            )
        if not submit_succeeded:
            LOG.error(
                "offload_submit_failed run_id=%s draft_id=%s error=%s result=%s",
                run_id,
                draft_id,
                error_detail or "Submit did not complete",
                result if isinstance(result, dict) else None,
            )
            raise RuntimeError(error_detail or "Submit did not complete")

        return {
            "run_id": run_id,
            "draft_id": draft_id,
            "submitted_id": result.get("submitted_id"),
            "success_count": result.get("success_count"),
        }

    def _mark_related_failure(self, job: dict[str, Any], error: str) -> None:
        run_id = _optional_str(job.get("run_id"))
        draft_id = _optional_str(job.get("draft_id"))
        job_type = _optional_str(job.get("job_type")) or "unknown"

        if run_id:
            self.ctx.supabase.runs.finalize_run(run_id, status="failed", error=error)
            self.ctx.services.tracing.complete_run(run_id)
        if not draft_id:
            return
        if job_type == "document_import":
            _save_draft_state(
                ctx=self.ctx,
                draft_id=draft_id,
                shop_domain=_optional_str(job.get("shop_domain")),
                fallback_run_id=run_id,
                extraction_status="failed",
                extraction_run_id=run_id,
                extraction_error=error,
            )
            return
        if job_type == "shopify_submit":
            _save_draft_state(
                ctx=self.ctx,
                draft_id=draft_id,
                shop_domain=_optional_str(job.get("shop_domain")),
                fallback_run_id=run_id,
                submit_status="failed",
                submit_run_id=run_id,
                submit_error=error,
            )

    async def _process_job(self, job: dict[str, Any]) -> dict[str, Any]:
        job_type = _optional_str(job.get("job_type")) or ""
        if job_type == "document_import":
            return await self._process_document_import(job)
        if job_type == "shopify_submit":
            return await self._process_shopify_submit(job)
        raise RuntimeError(f"Unsupported offload job type: {job_type or 'unknown'}")

    async def run_once(self) -> bool:
        job = self.ctx.supabase.runs.claim_next_offload_job(
            queue_name=self.queue_name,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if not isinstance(job, dict):
            LOG.debug("No queued jobs found on queue=%s", self.queue_name)
            signal_offload_queue(
                queue_name=self.queue_name,
                status="idle",
                backlog=_estimate_queue_backlog(self.ctx, self.queue_name),
            )
            return False

        job_id = _optional_str(job.get("job_id"))
        if not job_id:
            LOG.error("Claimed offload job without job_id")
            signal_offload_queue(
                queue_name=self.queue_name,
                status="failed",
                job_type=_optional_str(job.get("job_type")) or "unknown",
                error="Claimed offload job without job_id",
            )
            return True

        job_type = _optional_str(job.get("job_type")) or "unknown"
        signal_offload_queue(
            queue_name=self.queue_name,
            status="claimed",
            backlog=_estimate_queue_backlog(self.ctx, self.queue_name),
            job_id=job_id,
            job_type=job_type,
            run_id=_optional_str(job.get("run_id")),
            attempt_count=_safe_int(job.get("attempt_count"), 1, minimum=0),
            max_attempts=_safe_int(job.get("max_attempts"), 5, minimum=1),
        )
        with bind_observability_context(
            request_id=_optional_str(job.get("request_id")),
            correlation_id=_optional_str(job.get("correlation_id"))
            or _optional_str(job.get("run_id")),
        ) as (request_id, correlation_id):
            LOG.info(
                "Claimed offload job: job_id=%s type=%s queue=%s",
                job_id,
                job_type,
                self.queue_name,
            )

            transitioned = self.ctx.supabase.runs.transition_offload_workflow(job_id, "running")
            if not transitioned or str(transitioned.get("status") or "").lower() == "cancelled":
                return True

            try:
                result = await self._process_job(job)
                self.ctx.supabase.runs.transition_offload_workflow(job_id, "succeeded", result=result)
                LOG.info("Offload job succeeded: job_id=%s type=%s", job_id, job_type)
            except Exception as exc:
                error_message = _exception_message(exc)
                LOG.exception("Offload job failed: job_id=%s", job_id)
                attempt_count = _safe_int(job.get("attempt_count"), 1, minimum=0)
                max_attempts = _safe_int(job.get("max_attempts"), 5, minimum=1)
                should_retry = attempt_count < max_attempts and _is_retryable_job_failure(
                    job_type=job_type,
                    error_message=error_message,
                )
                signal_worker_job_failure(
                    queue_name=self.queue_name,
                    job_id=job_id,
                    job_type=job_type,
                    run_id=_optional_str(job.get("run_id")),
                    dead_letter=not should_retry,
                    attempt_count=attempt_count,
                    max_attempts=max_attempts,
                    error=error_message,
                )
                if should_retry:
                    retry_at = datetime.now(timezone.utc) + timedelta(
                        seconds=_compute_retry_delay_seconds(attempt_count)
                    )
                    self.ctx.supabase.runs.transition_offload_workflow(
                        job_id, "retryable", error=error_message,
                        available_at=retry_at.isoformat(), result={
                                "error": error_message,
                                "dead_letter": False,
                                "attempt_count": attempt_count,
                                "max_attempts": max_attempts,
                                "retry_at": retry_at.isoformat(),
                            },
                    )
                    continue_run_id = _optional_str(job.get("run_id"))
                    signal_offload_queue(
                        queue_name=self.queue_name,
                        status="retryable",
                        backlog=_estimate_queue_backlog(self.ctx, self.queue_name),
                        job_id=job_id,
                        job_type=job_type,
                        run_id=continue_run_id,
                        attempt_count=attempt_count,
                        max_attempts=max_attempts,
                        error=error_message,
                    )
                else:
                    try:
                        self._mark_related_failure(job, error_message)
                    except Exception:
                        LOG.exception(
                            "Failed marking related offload failure state for job_id=%s",
                            job_id,
                        )
                    self.ctx.supabase.runs.transition_offload_workflow(
                        job_id, "failed", error=error_message,
                        failure_code=_failure_code(error_message), result={
                                "error": error_message,
                                "dead_letter": True,
                                "attempt_count": attempt_count,
                                "max_attempts": max_attempts,
                            },
                    )
                    signal_offload_queue(
                        queue_name=self.queue_name,
                        status="failed",
                        backlog=_estimate_queue_backlog(self.ctx, self.queue_name),
                        job_id=job_id,
                        job_type=job_type,
                        run_id=_optional_str(job.get("run_id")),
                        attempt_count=attempt_count,
                        max_attempts=max_attempts,
                        error=error_message,
                    )
        return True

    async def run_forever(self) -> None:
        LOG.info(
            "Starting offload worker queue=%s worker_id=%s lease_seconds=%s poll_seconds=%s",
            self.queue_name,
            self.worker_id,
            self.lease_seconds,
            self.poll_seconds,
        )
        while True:
            try:
                processed = await self.run_once()
            except Exception:
                LOG.exception("Offload worker loop failed")
                processed = False
            if not processed:
                await asyncio.sleep(self.poll_seconds)
