import asyncio
import logging
import os
import socket
from typing import Any

from app_context import AppContext, get_app_context
from application.use_cases.drafts.get_product_draft import execute as get_draft_execute
from application.use_cases.drafts.save_product_draft import execute as save_draft_execute
from application.use_cases.files.get_file import execute as get_file_execute
from application.use_cases.processing.process_document import (
    execute as process_document_execute,
)
from application.use_cases.processing.submit_products import execute as submit_execute

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


def _save_draft_state(
    *,
    ctx: AppContext,
    draft_id: str,
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
    existing = get_draft_execute(supabase=ctx.supabase, draft_id=draft_id) or {}
    save_draft_execute(
        supabase=ctx.supabase,
        draft_id=draft_id,
        run_id=_optional_str_field(existing, "run_id") or fallback_run_id,
        import_mode=_optional_str_field(existing, "import_mode") or fallback_import_mode,
        draft_name=_optional_str_field(existing, "draft_name") or fallback_name,
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
        )
        result_payload = result.get("result") if isinstance(result, dict) else None

        output_file_id = None
        output_filename = None
        extracted_products: list[dict[str, Any]] | None = None
        if isinstance(result_payload, dict):
            output_file_id = _optional_str(result_payload.get("file_id"))
            output_filename = _optional_str(result_payload.get("filename"))
            products = result_payload.get("products")
            if isinstance(products, list):
                extracted_products = [
                    item for item in products if isinstance(item, dict)
                ]

        if draft_id:
            _save_draft_state(
                ctx=self.ctx,
                draft_id=draft_id,
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

        if draft_id:
            _save_draft_state(
                ctx=self.ctx,
                draft_id=draft_id,
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
        submit_succeeded = bool(
            isinstance(result, dict)
            and isinstance(result.get("submitted_id"), str)
            and result.get("submitted_id")
        )
        if draft_id:
            _save_draft_state(
                ctx=self.ctx,
                draft_id=draft_id,
                fallback_run_id=run_id,
                fallback_name=document_name,
                submit_status="succeeded" if submit_succeeded else "failed",
                submit_run_id=run_id,
                submit_error=None if submit_succeeded else "Submit did not complete",
            )
        if not submit_succeeded:
            raise RuntimeError("Submit did not complete")

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
            return False

        job_id = _optional_str(job.get("job_id"))
        if not job_id:
            LOG.error("Claimed offload job without job_id")
            return True

        job_type = _optional_str(job.get("job_type")) or "unknown"
        LOG.info(
            "Claimed offload job: job_id=%s type=%s queue=%s",
            job_id,
            job_type,
            self.queue_name,
        )

        self.ctx.supabase.runs.update_offload_job(
            job_id,
            {
                "status": "running",
                "error": None,
            },
        )

        try:
            result = await self._process_job(job)
            self.ctx.supabase.runs.update_offload_job(
                job_id,
                {
                    "status": "succeeded",
                    "error": None,
                    "result": result,
                },
            )
            LOG.info("Offload job succeeded: job_id=%s type=%s", job_id, job_type)
        except Exception as exc:
            error_message = str(exc)
            LOG.exception("Offload job failed: job_id=%s", job_id)
            try:
                self._mark_related_failure(job, error_message)
            except Exception:
                LOG.exception(
                    "Failed marking related offload failure state for job_id=%s",
                    job_id,
                )
            self.ctx.supabase.runs.update_offload_job(
                job_id,
                {
                    "status": "failed",
                    "error": error_message,
                    "result": {"error": error_message},
                },
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
