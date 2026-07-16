"""Agent file upload and processing routes (under `/agents/*`)."""

import json
import logging
import os
import uuid
from datetime import datetime, timezone


from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app_context import AppContext, get_ctx
from typing import Any
from application.services.document_formats import (
    classify_document,
    supported_extensions_display,
    validate_document_content,
)
from shared.observability import current_observability_fields
from .files_helper import generate_thumbnail_bytes
from .schemas import (
    BatchExtractSubmitAcceptedItem,
    BatchExtractSubmitError,
    BatchExtractSubmitRequest,
    BatchExtractSubmitResult,
    BulkDeletePayload,
    BulkDeleteResult,
    BulkUploadError,
    BulkUploadItem,
    BulkUploadResult,
)
from .utils import require_shop_domain, resolve_dev_billing_simulator_plan
from api.agents.billing import _get_billing_svc

router = APIRouter()
LOG = logging.getLogger(__name__)
MERCHANT_UPLOAD_FILE_ORIGIN = "merchant_upload"


@router.post("/source-link-traces", summary="Record a sampled source-link trace event")
async def record_source_link_trace_event(
    request: Request,
    attempt_id: str = Form(...),
    component: str = Form("frontend"),
    stage: str = Form(...),
    status: str = Form("info"),
    source_file_id: str | None = Form(None),
    highlight_file_id: str | None = Form(None),
    details_json: str | None = Form(None),
) -> dict[str, bool]:
    from services.source_link_trace import record, valid_attempt_id

    if not valid_attempt_id(attempt_id):
        raise HTTPException(status_code=422, detail="Invalid source-link trace attempt id")
    details: dict[str, Any] = {}
    if details_json:
        try:
            parsed = json.loads(details_json)
            if isinstance(parsed, dict):
                details = parsed
        except json.JSONDecodeError:
            pass
    fields = current_observability_fields()
    record(
        component=component,
        stage=stage,
        status=status,
        attempt_id=attempt_id,
        shop_domain=require_shop_domain(request),
        source_file_id=source_file_id,
        highlight_file_id=highlight_file_id,
        request_id=fields.get("request_id"),
        correlation_id=fields.get("correlation_id"),
        details=details,
    )
    return {"recorded": True}


def _has_processing_access(request: Request, billing_svc: Any, shop_domain: str) -> bool:
    return resolve_dev_billing_simulator_plan(request) is not None or billing_svc.can_process(
        shop_domain
    )


def _optional_str(entry: dict[str, object] | None, key: str) -> str | None:
    if not entry:
        return None
    value = entry.get(key)
    return value if isinstance(value, str) else None


def _required_str(entry: dict[str, object], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str):
        raise HTTPException(status_code=500, detail=f"Stored file {key} is invalid")
    return value


def _draft_products(entry: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(entry, dict):
        return []
    raw_products = entry.get("products")
    if not isinstance(raw_products, list):
        return []
    return [item for item in raw_products if isinstance(item, dict)]


def _first_non_empty_str(*values: str | None) -> str | None:
    for value in values:
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


def _resolved_draft_name(
    *,
    draft_id: str,
    existing_draft_name: str | None,
    fallback_name: str | None,
    existing_input_filename: str | None,
    fallback_input_filename: str | None,
) -> str:
    return (
        _first_non_empty_str(
            existing_draft_name,
            fallback_name,
            existing_input_filename,
            fallback_input_filename,
        )
        or f"draft-{draft_id[:8]}.xlsx"
    )


def _find_active_import_draft_for_file(
    *, ctx: AppContext, file_id: str, shop_domain: str | None = None
) -> dict[str, Any] | None:
    from application.use_cases.drafts.list_product_drafts import execute as list_drafts_execute

    def _is_matching_active_draft(draft: dict[str, Any]) -> bool:
        if _optional_str(draft, "input_file_id") != file_id:
            return False
        status = (_optional_str(draft, "extraction_status") or "").strip().lower()
        if status not in {"queued", "running"}:
            return False
        return bool(_optional_str(draft, "draft_id"))

    drafts = list_drafts_execute(
        supabase=ctx.supabase,
        limit=1000,
        offset=0,
        shop_domain=shop_domain,
    )
    for draft in drafts:
        if isinstance(draft, dict) and _is_matching_active_draft(draft):
            return draft

    service = getattr(ctx.supabase, "_service", None)
    memory_drafts = getattr(service, "product_drafts", None)
    if isinstance(memory_drafts, dict):
        for draft in memory_drafts.values():
            if isinstance(draft, dict) and _is_matching_active_draft(draft):
                return draft
    return None


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
    from application.use_cases.drafts.get_product_draft import execute as get_draft_execute
    from application.use_cases.drafts.save_product_draft import execute as save_draft_execute

    existing = (
        get_draft_execute(
            supabase=ctx.supabase,
            draft_id=draft_id,
            shop_domain=shop_domain,
        )
        or {}
    )
    existing_run_id = _optional_str(existing, "run_id")
    existing_import_mode = _optional_str(existing, "import_mode")
    existing_draft_name = _optional_str(existing, "draft_name")
    existing_input_file_id = _optional_str(existing, "input_file_id")
    existing_input_filename = _optional_str(existing, "input_filename")
    existing_output_file_id = _optional_str(existing, "output_file_id")
    existing_output_filename = _optional_str(existing, "output_filename")
    existing_extraction_status = _optional_str(existing, "extraction_status")
    existing_extraction_run_id = _optional_str(existing, "extraction_run_id")
    existing_extraction_error = _optional_str(existing, "extraction_error")
    existing_submit_status = _optional_str(existing, "submit_status")
    existing_submit_run_id = _optional_str(existing, "submit_run_id")
    existing_submit_error = _optional_str(existing, "submit_error")
    existing_shop_domain = _optional_str(existing, "shop_domain")
    normalized_fallback_name = fallback_name
    normalized_fallback_input_filename = fallback_input_filename
    if (
        isinstance(fallback_input_file_id, str)
        and fallback_input_file_id
        and normalized_fallback_name == fallback_input_file_id
    ):
        normalized_fallback_name = None
    if (
        isinstance(fallback_input_file_id, str)
        and fallback_input_file_id
        and normalized_fallback_input_filename == fallback_input_file_id
    ):
        normalized_fallback_input_filename = None
    resolved_draft_name = _resolved_draft_name(
        draft_id=draft_id,
        existing_draft_name=existing_draft_name,
        fallback_name=normalized_fallback_name,
        existing_input_filename=existing_input_filename,
        fallback_input_filename=normalized_fallback_input_filename,
    )

    save_draft_execute(
        supabase=ctx.supabase,
        draft_id=draft_id,
        run_id=existing_run_id or fallback_run_id,
        import_mode=existing_import_mode or fallback_import_mode,
        draft_name=resolved_draft_name,
        shop_domain=existing_shop_domain or shop_domain,
        input_file_id=existing_input_file_id or fallback_input_file_id,
        input_filename=existing_input_filename or normalized_fallback_input_filename,
        output_file_id=output_file_id if output_file_id is not None else existing_output_file_id,
        output_filename=(
            output_filename if output_filename is not None else existing_output_filename
        ),
        extraction_status=(
            extraction_status
            if extraction_status is not None
            else existing_extraction_status
        ),
        extraction_run_id=(
            extraction_run_id
            if extraction_run_id is not None
            else existing_extraction_run_id
        ),
        extraction_error=(
            extraction_error if extraction_error is not None else existing_extraction_error
        ),
        submit_status=submit_status if submit_status is not None else existing_submit_status,
        submit_run_id=submit_run_id if submit_run_id is not None else existing_submit_run_id,
        submit_error=submit_error if submit_error is not None else existing_submit_error,
        require_lifecycle_columns=True,
        products=products if products is not None else _draft_products(existing),
    )


async def _run_import_in_background(
    *,
    ctx: AppContext,
    run_id: str,
    draft_id: str,
    file_id: str,
    file_bytes: bytes,
    input_name: str | None,
    input_content_type: str | None,
    collabora_url: str | None,
    extraction_mode: str,
    write_to_file: bool,
    output_path: str | None,
    shop_domain: str | None,
) -> None:
    from application.use_cases.processing.process_document import (
        execute as process_document_execute,
    )

    _save_draft_state(
        ctx=ctx,
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

    try:
        result = await process_document_execute(
            supabase=ctx.supabase,
            llm=ctx.services.llm,
            tracing=ctx.services.tracing,
            ctx=ctx,
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
        payload = result.get("result") if isinstance(result, dict) else None
        output_file_id = None
        output_filename = None
        extracted_products: list[dict[str, Any]] | None = None
        if isinstance(payload, dict):
            if isinstance(payload.get("file_id"), str):
                output_file_id = payload["file_id"]
            if isinstance(payload.get("filename"), str):
                output_filename = payload["filename"]
            if isinstance(payload.get("products"), list):
                extracted_products = [
                    item for item in payload["products"] if isinstance(item, dict)
                ]

        _save_draft_state(
            ctx=ctx,
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
    except Exception as exc:
        _save_draft_state(
            ctx=ctx,
            draft_id=draft_id,
            shop_domain=shop_domain,
            fallback_run_id=run_id,
            fallback_name=input_name,
            fallback_input_file_id=file_id,
            fallback_input_filename=input_name,
            extraction_status="failed",
            extraction_run_id=run_id,
            extraction_error=str(exc),
        )
        LOG.exception(
            "Background import failed for run_id=%s draft_id=%s",
            run_id,
            draft_id,
        )


def _bulk_upload_error_code(status_code: int) -> str:
    if status_code == 415:
        return "unsupported_file_type"
    if status_code >= 500:
        return "processing_failed"
    return f"http_{status_code}"


def _upload_failure_code(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        if exc.status_code == 415:
            return "unsupported_file_type"
        if exc.status_code == 422:
            return "invalid_file_content"
        if exc.status_code == 402:
            return "upload_access_denied"
        if "conversion failed" in str(exc.detail).lower():
            return "upload_conversion_failed"
    return "upload_persistence_failed"


def _record_failed_upload_activity(
    *,
    ctx: AppContext,
    shop_domain: str,
    filename: str | None,
    content_type: str | None,
    size: int | None,
    error: Exception | str,
    started_at: datetime,
) -> None:
    """Persist a failed upload without allowing telemetry failure to mask the API error."""
    try:
        run_id = str(uuid.uuid4())
        ended_at = datetime.now(timezone.utc)
        message = str(error.detail) if isinstance(error, HTTPException) else str(error)
        failure_code = _upload_failure_code(error) if isinstance(error, Exception) else "upload_failed"
        observability = current_observability_fields()
        duration_ms = max(0, int((ended_at - started_at).total_seconds() * 1000))
        ctx.supabase.runs.create_or_update_run(
            run_id,
            {
                "source": "file_upload",
                "status": "failed",
                "shop_domain": shop_domain,
                "input_filename": filename,
                "input_content_type": content_type,
                "input_size_bytes": size,
                "started_at": started_at.isoformat(),
                "ended_at": ended_at.isoformat(),
                "duration_ms": duration_ms,
                "attempt": 1,
                "error": message,
                "failure_code": failure_code,
                "failure_message": message,
                **observability,
            },
        )
        ctx.supabase.runs.append_run_event(
            run_id,
            {
                "ts": ended_at.isoformat(),
                "phase": "upload_failed",
                "level": "error",
                "message": "File upload failed",
                "error": message,
                **observability,
            },
            1,
        )
    except Exception:
        LOG.exception("Failed recording upload activity for %s", filename)


def _queue_batch_extract_submit_for_file(
    *,
    ctx: AppContext,
    file_id: str,
    import_mode: str,
    extraction_mode: str,
    auto_submit: bool,
    shop_domain: str | None = None,
) -> BatchExtractSubmitAcceptedItem:
    from application.use_cases.files.get_file import execute as get_file_execute

    observability_fields = current_observability_fields()
    file_entry = get_file_execute(supabase=ctx.supabase, file_id=file_id)
    if not isinstance(file_entry, dict):
        raise LookupError("File not found")
    file_content = file_entry.get("content")
    if not isinstance(file_content, (bytes, bytearray)):
        raise RuntimeError("Stored file content is invalid")
    input_name = _optional_str(file_entry, "name")
    input_content_type = _optional_str(file_entry, "content_type")
    file_size = len(file_content)

    active_draft = _find_active_import_draft_for_file(
        ctx=ctx,
        file_id=file_id,
        shop_domain=shop_domain,
    )
    active_draft_id = _optional_str(active_draft, "draft_id")
    if active_draft and active_draft_id:
        extraction_run_id = _first_non_empty_str(
            _optional_str(active_draft, "extraction_run_id"),
            _optional_str(active_draft, "run_id"),
        ) or str(uuid.uuid4())
        submit_run_id = _first_non_empty_str(_optional_str(active_draft, "submit_run_id"))
        if auto_submit and not submit_run_id:
            submit_run_id = str(uuid.uuid4())
        if auto_submit and submit_run_id:
            _save_draft_state(
                ctx=ctx,
                draft_id=active_draft_id,
                shop_domain=shop_domain,
                fallback_run_id=extraction_run_id,
                fallback_import_mode=(
                    _first_non_empty_str(_optional_str(active_draft, "import_mode"))
                    or import_mode
                ),
                fallback_name=input_name,
                fallback_input_file_id=file_id,
                fallback_input_filename=input_name,
                submit_run_id=submit_run_id,
                submit_error=None,
            )
        elif not _first_non_empty_str(_optional_str(active_draft, "draft_name")):
            _save_draft_state(
                ctx=ctx,
                draft_id=active_draft_id,
                shop_domain=shop_domain,
                fallback_run_id=extraction_run_id,
                fallback_import_mode=(
                    _first_non_empty_str(_optional_str(active_draft, "import_mode"))
                    or import_mode
                ),
                fallback_name=input_name,
                fallback_input_file_id=file_id,
                fallback_input_filename=input_name,
            )
        return BatchExtractSubmitAcceptedItem(
            index=-1,
            file_id=file_id,
            draft_id=active_draft_id,
            extraction_run_id=extraction_run_id,
            submit_run_id=submit_run_id or str(uuid.uuid4()),
        )

    extraction_run_id = str(uuid.uuid4())
    draft_id = str(uuid.uuid4())
    submit_run_id = str(uuid.uuid4())
    _save_draft_state(
        ctx=ctx,
        draft_id=draft_id,
        shop_domain=shop_domain,
        fallback_run_id=extraction_run_id,
        fallback_import_mode=import_mode,
        fallback_name=input_name,
        fallback_input_file_id=file_id,
        fallback_input_filename=input_name,
        products=[],
        extraction_status="queued",
        extraction_run_id=extraction_run_id,
        extraction_error=None,
        submit_status=None,
        submit_run_id=submit_run_id if auto_submit else None,
        submit_error=None,
    )
    ctx.supabase.runs.create_or_update_run(
        extraction_run_id,
        {
            "status": "queued",
            "source": "document_import",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "input_file_id": file_id,
            "input_filename": input_name,
            "input_content_type": input_content_type,
            "input_size_bytes": file_size,
            "attempt": 1,
            "shop_domain": shop_domain,
            **observability_fields,
        },
    )
    ctx.supabase.runs.enqueue_offload_job(
        str(uuid.uuid4()),
        {
            "queue_name": "offload",
            "job_type": "document_import",
            "status": "queued",
            "run_id": extraction_run_id,
            "draft_id": draft_id,
            "file_id": file_id,
            "shop_domain": shop_domain,
            **observability_fields,
            "payload": {
                "input_filename": input_name,
                "input_content_type": input_content_type,
                "extraction_mode": extraction_mode,
                "write_to_file": False,
                "output_path": None,
                "collabora_url": None,
                "import_mode": import_mode,
                "auto_submit": bool(auto_submit),
                "submit_run_id": submit_run_id if auto_submit else None,
            },
        },
        require_persistent_queue=True,
    )
    return BatchExtractSubmitAcceptedItem(
        index=-1,
        file_id=file_id,
        draft_id=draft_id,
        extraction_run_id=extraction_run_id,
        submit_run_id=submit_run_id,
    )


async def _prepare_uploaded_file(
    file: UploadFile, *, ctx: AppContext
) -> dict[str, Any]:
    file_bytes = await file.read()
    file_id = str(uuid.uuid4())
    original_name = file.filename or "document.xlsx"
    original_content_type = file.content_type or ""
    document_format = classify_document(
        filename=original_name,
        content_type=original_content_type,
        file_bytes=file_bytes,
    )
    if not document_format.is_supported:
        raise HTTPException(
            status_code=415,
            detail=(
                "Unsupported file type. Supported extensions: "
                f"{supported_extensions_display()}"
            ),
        )

    try:
        validate_document_content(document_format, file_bytes=file_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    stored_name = original_name
    stored_content_type = (
        file.content_type
        or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    if document_format.kind == "csv":
        collabora_url = os.getenv("COLLABORA_URL", "http://localhost:9980")
        try:
            from application.use_cases.collabora.convert_csv_to_excel import (
                execute as convert_csv_execute,
            )

            file_bytes = await convert_csv_execute(
                collabora=ctx.services.collabora,
                csv_bytes=file_bytes,
                collabora_base_url=collabora_url,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"CSV conversion failed: {exc}")
        base, _ = os.path.splitext(original_name)
        stored_name = f"{base or 'document'}.xlsx"
        stored_content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    elif document_format.kind == "spreadsheet_legacy":
        collabora_url = os.getenv("COLLABORA_URL", "http://localhost:9980")
        try:
            file_bytes = await ctx.services.collabora.convert_document_to_xlsx_collabora(
                file_bytes,
                filename=original_name,
                content_type=original_content_type or "application/octet-stream",
                collabora_base_url=collabora_url,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Spreadsheet conversion failed: {exc}"
            )
        base, _ = os.path.splitext(original_name)
        stored_name = f"{base or 'document'}.xlsx"
        stored_content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    stored_format = classify_document(
        filename=stored_name,
        content_type=stored_content_type,
        file_bytes=file_bytes,
    )
    try:
        validate_document_content(stored_format, file_bytes=file_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "file_id": file_id,
        "name": stored_name,
        "content_type": stored_content_type,
        "content": file_bytes,
        "size": len(file_bytes),
    }


@router.get("/files", summary="List uploaded files")
async def list_uploaded_files(
    request: Request,
    limit: int = 1000,
    offset: int = 0,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    """List all uploaded files."""
    from application.use_cases.files.list_files import execute as list_files_execute

    resolved_limit = max(1, min(limit, 5000))
    resolved_offset = max(0, offset)
    shop_domain = require_shop_domain(request)
    files = list_files_execute(
        supabase=ctx.supabase,
        limit=resolved_limit,
        offset=resolved_offset,
        shop_domain=shop_domain,
    )
    return {"files": files}


@router.get("/collabora-url", summary="Get current Collabora URL")
async def get_collabora_url(
    request: Request,
    trace_attempt_id: str | None = None,
    ctx: AppContext = Depends(get_ctx),
):
    """Get the current Collabora URL and WOPI base URL for interactive viewer."""
    from application.use_cases.collabora.get_collabora_url import (
        execute as get_collabora_execute,
    )

    try:
        ctx.services.collabora.readiness()
        payload = get_collabora_execute(collabora=ctx.services.collabora)
        from urllib.parse import urlparse
        from services.source_link_trace import record
        record(
            component="backend",
            stage="viewer_url_resolved",
            status="ok",
            attempt_id=trace_attempt_id,
            shop_domain=request.headers.get("x-shop-domain"),
            details={"url_host": urlparse(str(payload.get("collabora_url") or "")).hostname or ""},
        )
        return payload
    except RuntimeError as exc:
        from services.source_link_trace import record
        record(
            component="backend",
            stage="viewer_url_unavailable",
            status="error",
            attempt_id=trace_attempt_id,
            shop_domain=request.headers.get("x-shop-domain"),
            details={"reason": str(exc)},
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": str(exc),
                "code": getattr(exc, "code", "COLLABORA_UNAVAILABLE"),
            },
        )


@router.post("/upload", summary="Upload a file for preview and processing")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    """Upload a file and return a file_id for WOPI preview."""
    shop_domain = require_shop_domain(request)
    started_at = datetime.now(timezone.utc)
    prepared: dict[str, Any] | None = None
    try:
        billing_svc = _get_billing_svc(ctx)
        simulator_plan = resolve_dev_billing_simulator_plan(request)
        if not _has_processing_access(request, billing_svc, shop_domain):
            raise HTTPException(status_code=402, detail="Active subscription required to process files")

        prepared = await _prepare_uploaded_file(file, ctx=ctx)

        from application.use_cases.files.save_file import execute as save_file_execute

        save_file_execute(
            supabase=ctx.supabase,
            file_id=prepared["file_id"],
            name=prepared["name"],
            content=prepared["content"],
            content_type=prepared["content_type"],
            file_origin=MERCHANT_UPLOAD_FILE_ORIGIN,
            shop_domain=shop_domain,
        )
        thumbnail_generated = False
    except Exception as exc:
        _record_failed_upload_activity(
            ctx=ctx,
            shop_domain=shop_domain,
            filename=(prepared or {}).get("name") or file.filename,
            content_type=(prepared or {}).get("content_type") or file.content_type,
            size=(prepared or {}).get("size") or getattr(file, "size", None),
            error=exc,
            started_at=started_at,
        )
        raise

    if simulator_plan is None:
        try:
            billing_svc.increment_usage(shop_domain, files=1, tokens=0)
        except Exception as usage_err:
            LOG.warning("Failed to increment usage for %s: %s", shop_domain, usage_err)

    return {
        "file_id": prepared["file_id"],
        "filename": prepared["name"],
        "size": prepared["size"],
        "thumbnail_generated": thumbnail_generated,
    }


@router.post("/upload/bulk", summary="Upload multiple files for preview and processing")
async def upload_files_bulk(
    request: Request,
    files: list[UploadFile] = File(...),
    ctx: AppContext = Depends(get_ctx),
) -> BulkUploadResult:
    shop_domain = require_shop_domain(request)
    billing_svc = _get_billing_svc(ctx)
    simulator_plan = resolve_dev_billing_simulator_plan(request)
    if not _has_processing_access(request, billing_svc, shop_domain):
        exc = HTTPException(status_code=402, detail="Active subscription required to process files")
        for file in files:
            _record_failed_upload_activity(
                ctx=ctx,
                shop_domain=shop_domain,
                filename=file.filename,
                content_type=file.content_type,
                size=getattr(file, "size", None),
                error=exc,
                started_at=datetime.now(timezone.utc),
            )
        raise exc

    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")

    prepared_items: list[dict[str, Any]] = []
    errors: list[BulkUploadError] = []

    for index, file in enumerate(files):
        item_started_at = datetime.now(timezone.utc)
        try:
            prepared = await _prepare_uploaded_file(file, ctx=ctx)
            prepared["index"] = index
            prepared_items.append(prepared)
        except HTTPException as exc:
            _record_failed_upload_activity(
                ctx=ctx,
                shop_domain=shop_domain,
                filename=file.filename or f"file-{index + 1}",
                content_type=file.content_type,
                size=getattr(file, "size", None),
                error=exc,
                started_at=item_started_at,
            )
            errors.append(
                BulkUploadError(
                    index=index,
                    filename=file.filename or f"file-{index + 1}",
                    error=str(exc.detail),
                    code=_bulk_upload_error_code(exc.status_code),
                )
            )

    if prepared_items:
        payloads = [
            {
                "file_id": item["file_id"],
                "name": item["name"],
                "content": item["content"],
                "content_type": item["content_type"],
                "file_origin": MERCHANT_UPLOAD_FILE_ORIGIN,
                "shop_domain": shop_domain,
            }
            for item in prepared_items
        ]
        try:
            ctx.supabase.file.save_files(payloads)
        except Exception as exc:
            for item in prepared_items:
                _record_failed_upload_activity(
                    ctx=ctx,
                    shop_domain=shop_domain,
                    filename=item.get("name"),
                    content_type=item.get("content_type"),
                    size=item.get("size"),
                    error=exc,
                    started_at=datetime.now(timezone.utc),
                )
            raise HTTPException(
                status_code=500, detail=f"Failed saving uploaded files: {exc}"
            )

    uploaded = [
        BulkUploadItem(
            index=item["index"],
            file_id=item["file_id"],
            filename=item["name"],
            size=item["size"],
        )
        for item in prepared_items
    ]
    successful_count = len(uploaded)
    if successful_count > 0 and simulator_plan is None:
        try:
            billing_svc.increment_usage(shop_domain, files=successful_count, tokens=0)
        except Exception as usage_err:
            LOG.warning("Failed to increment usage for %s: %s", shop_domain, usage_err)

    return BulkUploadResult(
        total=len(files),
        succeeded=len(uploaded),
        failed=len(errors),
        uploaded=uploaded,
        errors=errors,
    )


@router.post(
    "/import/batch",
    summary="Queue extraction and auto-submit for multiple uploaded files",
    status_code=202,
)
async def queue_batch_extract_submit(
    request: Request,
    payload: BatchExtractSubmitRequest,
    ctx: AppContext = Depends(get_ctx),
) -> BatchExtractSubmitResult:
    resolved_shop_domain = require_shop_domain(request)
    accepted: list[BatchExtractSubmitAcceptedItem] = []
    errors: list[BatchExtractSubmitError] = []

    for index, raw_file_id in enumerate(payload.file_ids):
        file_id = raw_file_id.strip()
        if not file_id:
            errors.append(
                BatchExtractSubmitError(
                    index=index,
                    file_id=raw_file_id,
                    error="File ID is required",
                    code="invalid_file_id",
                )
            )
            continue

        try:
            queued_item = _queue_batch_extract_submit_for_file(
                ctx=ctx,
                file_id=file_id,
                import_mode=payload.import_mode,
                extraction_mode=payload.extraction_mode,
                auto_submit=payload.auto_submit,
                shop_domain=resolved_shop_domain,
            )
        except LookupError:
            errors.append(
                BatchExtractSubmitError(
                    index=index,
                    file_id=file_id,
                    error="File not found",
                    code="file_not_found",
                )
            )
            continue
        except RuntimeError as exc:
            message = str(exc).strip() or "Failed queueing batch import"
            code = (
                "invalid_file_content"
                if "content is invalid" in message.lower()
                else "queue_failed"
            )
            errors.append(
                BatchExtractSubmitError(
                    index=index,
                    file_id=file_id,
                    error=message,
                    code=code,
                )
            )
            continue
        except Exception as exc:
            errors.append(
                BatchExtractSubmitError(
                    index=index,
                    file_id=file_id,
                    error=str(exc) or "Failed queueing batch import",
                    code="queue_failed",
                )
            )
            continue

        accepted.append(
            BatchExtractSubmitAcceptedItem(
                index=index,
                file_id=queued_item.file_id,
                draft_id=queued_item.draft_id,
                extraction_run_id=queued_item.extraction_run_id,
                submit_run_id=queued_item.submit_run_id,
            )
        )

    return BatchExtractSubmitResult(
        total=len(payload.file_ids),
        queued=len(accepted),
        failed=len(errors),
        accepted=accepted,
        errors=errors,
    )


@router.post("/excel", summary="Process an Excel file through the AI agent workflow")
@router.post("/import", summary="Process a document through the AI agent workflow")
async def process_excel(
    request: Request,
    run_id: str | None = Form(None),
    file_id: str | None = Form(None),
    file: UploadFile | None = File(None),
    collabora_url: str | None = Form(None),
    extraction_mode: str = Form("per_sheet"),
    write_to_file: bool = Form(False),
    output_path: str | None = Form(None),
    offload: bool = Form(False),
    shop_domain: str | None = Form(None),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    """Thin controller delegating to application.use_cases.processing.process_document.execute.

    Accepts either file_id or direct upload.
    """
    tenant = require_shop_domain(request, shop_domain)
    billing_svc = _get_billing_svc(ctx)
    if not _has_processing_access(request, billing_svc, tenant):
        raise HTTPException(status_code=402, detail="Active subscription required to process files")

    from application.use_cases.processing.process_document import (
        execute as process_document_execute,
    )

    # Resolve file bytes and metadata
    file_entry: dict[str, object] | None = None
    file_bytes_data: bytes | None = None
    if file_id:
        from application.use_cases.files.get_file import execute as get_file_execute

        file_entry = get_file_execute(supabase=ctx.supabase, file_id=file_id)
        if not file_entry:
            raise HTTPException(status_code=404, detail="File not found")
        file_content = file_entry.get("content")
        if not isinstance(file_content, (bytes, bytearray)):
            raise HTTPException(
                status_code=500, detail="Stored file content is invalid"
            )
        file_bytes_data = bytes(file_content)
    elif file:
        file_bytes_data = await file.read()
    else:
        raise HTTPException(
            status_code=400,
            detail="Either file_id (from /agents/upload) or file upload is required",
        )

    if file_bytes_data is None:
        raise HTTPException(status_code=500, detail="File content is missing")

    input_name = file.filename if file else _optional_str(file_entry, "name")
    input_content_type = (
        file.content_type if file else _optional_str(file_entry, "content_type")
    )
    import_format = classify_document(
        filename=input_name,
        content_type=input_content_type,
        file_bytes=file_bytes_data,
    )
    if not import_format.is_supported:
        raise HTTPException(
            status_code=415,
            detail=(
                "Unsupported file type. Supported extensions: "
                f"{supported_extensions_display()}"
            ),
        )

    if offload:
        observability_fields = current_observability_fields()
        if not file_id:
            raise HTTPException(
                status_code=400,
                detail="Offloaded imports require file_id from /agents/upload",
            )
        active_draft = _find_active_import_draft_for_file(
            ctx=ctx,
            file_id=file_id,
            shop_domain=tenant,
        )
        active_draft_id = _optional_str(active_draft, "draft_id")
        if active_draft and active_draft_id:
            active_run_id = _first_non_empty_str(
                _optional_str(active_draft, "extraction_run_id"),
                _optional_str(active_draft, "run_id"),
                run_id,
            ) or str(uuid.uuid4())
            active_status = (
                _first_non_empty_str(_optional_str(active_draft, "extraction_status"))
                or "queued"
            ).lower()
            if active_status not in {"queued", "running"}:
                active_status = "queued"
            if not _first_non_empty_str(_optional_str(active_draft, "draft_name")):
                _save_draft_state(
                    ctx=ctx,
                    draft_id=active_draft_id,
                    shop_domain=tenant,
                    fallback_run_id=active_run_id,
                    fallback_import_mode=(
                        _first_non_empty_str(_optional_str(active_draft, "import_mode"))
                        or "auto"
                    ),
                    fallback_name=input_name,
                    fallback_input_file_id=file_id,
                    fallback_input_filename=input_name,
                )
            return JSONResponse(
                status_code=202,
                content={
                    "run_id": active_run_id,
                    "draft_id": active_draft_id,
                    "status": active_status,
                    "file_id": file_id,
                },
            )
        effective_run_id = run_id or str(uuid.uuid4())
        draft_id = str(uuid.uuid4())
        try:
            _save_draft_state(
                ctx=ctx,
                draft_id=draft_id,
                shop_domain=tenant,
                fallback_run_id=effective_run_id,
                fallback_import_mode="auto",
                fallback_name=input_name,
                fallback_input_file_id=file_id,
                fallback_input_filename=input_name,
                products=[],
                extraction_status="queued",
                extraction_run_id=effective_run_id,
                extraction_error=None,
                submit_status=None,
                submit_run_id=None,
                submit_error=None,
            )
            ctx.supabase.runs.create_or_update_run(
                effective_run_id,
                {
                    "status": "queued",
                    "source": "document_import",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "input_file_id": file_id,
                    "input_filename": input_name,
                    "input_content_type": input_content_type,
                    "input_size_bytes": len(file_bytes_data),
                    "attempt": 1,
                    "shop_domain": tenant,
                    **observability_fields,
                },
            )
            ctx.supabase.runs.enqueue_offload_job(
                str(uuid.uuid4()),
                {
                    "queue_name": "offload",
                    "job_type": "document_import",
                    "status": "queued",
                    "run_id": effective_run_id,
                    "draft_id": draft_id,
                    "file_id": file_id,
                    "shop_domain": tenant,
                    **observability_fields,
                    "payload": {
                        "input_filename": input_name,
                        "input_content_type": input_content_type,
                        "extraction_mode": extraction_mode,
                        "write_to_file": bool(write_to_file),
                        "output_path": output_path,
                        "collabora_url": collabora_url,
                    },
                },
                require_persistent_queue=True,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return JSONResponse(
            status_code=202,
            content={
                "run_id": effective_run_id,
                "draft_id": draft_id,
                "status": "queued",
                "file_id": file_id,
            },
        )

    result = await process_document_execute(
        supabase=ctx.supabase,
        llm=ctx.services.llm,
        tracing=ctx.services.tracing,
        ctx=ctx,
        file_bytes=file_bytes_data,
        input_name=input_name,
        input_content_type=input_content_type,
        run_id=run_id,
        collabora_url=collabora_url,
        extraction_mode=extraction_mode,
        write_to_file=write_to_file,
        output_path=output_path,
        shop_domain=tenant,
    )

    try:
        billing_svc.increment_usage(tenant, files=1, tokens=0)
    except Exception as usage_err:
        LOG.warning("Failed to increment usage for %s: %s", tenant, usage_err)

    return result


@router.get("/files/{file_id}", summary="Get file info")
async def get_file_info(
    file_id: str, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    """Get information about an uploaded file."""
    from application.use_cases.files.get_file import execute as get_file_execute

    file_entry = get_file_execute(supabase=ctx.supabase, file_id=file_id)
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")
    file_content = file_entry.get("content")
    if not isinstance(file_content, (bytes, bytearray)):
        raise HTTPException(status_code=500, detail="Stored file content is invalid")
    file_name = _required_str(file_entry, "name")
    file_content_type = _required_str(file_entry, "content_type")
    file_storage_path = _required_str(file_entry, "storage_path")

    return {
        "file_id": file_id,
        "filename": file_name,
        "size": len(file_content),
        "content_type": file_content_type,
        "storage_path": file_storage_path,
        "thumbnail_storage_path": file_entry.get("thumbnail_storage_path"),
    }


@router.post(
    "/files/{file_id}/source-highlight",
    summary="Create highlighted spreadsheet preview for source coordinates",
)
async def create_source_highlight(
    file_id: str,
    request: Request,
    sheet: str | None = Form(None),
    cell: str | None = Form(None),
    cell_range: str | None = Form(None),
    source_refs_json: str | None = Form(None),
    preferred_sheet: str | None = Form(None),
    highlight_file_id: str | None = Form(None),
    trace_attempt_id: str | None = Form(None),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.files.create_source_highlight_file import (
        execute as create_source_highlight_execute,
    )

    parsed_source_refs: list[dict[str, Any]] | None = None
    if source_refs_json and source_refs_json.strip():
        try:
            decoded_source_refs = json.loads(source_refs_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=422, detail=f"Invalid source_refs_json payload: {exc}"
            )
        if not isinstance(decoded_source_refs, list):
            raise HTTPException(
                status_code=422, detail="source_refs_json must be a JSON array"
            )
        if any(not isinstance(item, dict) for item in decoded_source_refs):
            raise HTTPException(
                status_code=422,
                detail="source_refs_json entries must be JSON objects",
            )
        parsed_source_refs = decoded_source_refs

    tenant = require_shop_domain(request)
    from services.source_link_trace import record as record_source_link_trace
    trace_fields = current_observability_fields()
    record_source_link_trace(
        component="backend",
        stage="highlight_start",
        attempt_id=trace_attempt_id,
        shop_domain=tenant,
        source_file_id=file_id,
        highlight_file_id=highlight_file_id,
        request_id=trace_fields.get("request_id"),
        correlation_id=trace_fields.get("correlation_id"),
        details={"range_count": len(parsed_source_refs or [])},
    )
    def record_highlight_failure(exc: Exception) -> None:
        record_source_link_trace(
            component="backend",
            stage="highlight_failed",
            status="error",
            attempt_id=trace_attempt_id,
            shop_domain=tenant,
            source_file_id=file_id,
            highlight_file_id=highlight_file_id,
            details={"error": type(exc).__name__, "reason": str(exc)},
        )

    try:
        result = await create_source_highlight_execute(
            supabase=ctx.supabase,
            collabora=ctx.services.collabora,
            source_file_id=file_id,
            sheet=sheet,
            cell=cell,
            cell_range=cell_range,
            source_refs=parsed_source_refs,
            preferred_sheet=preferred_sheet,
            highlight_file_id=highlight_file_id,
        )
        ctx.supabase.file.set_file_shop_domain(str(result["file_id"]), tenant)
        record_source_link_trace(
            component="backend",
            stage="highlight_complete",
            status="ok",
            attempt_id=trace_attempt_id,
            shop_domain=tenant,
            source_file_id=file_id,
            highlight_file_id=str(result["file_id"]),
            request_id=trace_fields.get("request_id"),
            correlation_id=trace_fields.get("correlation_id"),
            details={
                "sheet": result.get("sheet"),
                "target": result.get("cell_range"),
                "range_count": len(result.get("selection_ranges") or []),
            },
        )
        return result
    except LookupError as exc:
        record_highlight_failure(exc)
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        record_highlight_failure(exc)
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        record_highlight_failure(exc)
        raise HTTPException(
            status_code=500, detail=f"Source highlight generation failed: {exc}"
        )


@router.get(
    "/files/{file_id}/source-target",
    summary="Resolve non-spreadsheet Collabora source target",
)
async def resolve_source_target(
    file_id: str,
    request: Request,
    value: str | None = None,
    document_kind: str | None = None,
    page: int | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.files.resolve_collabora_source_target import (
        execute as resolve_source_target_execute,
    )

    if page is not None and page < 1:
        raise HTTPException(status_code=422, detail="page must be greater than 0")

    require_shop_domain(request)
    try:
        collabora_url = os.getenv("COLLABORA_URL", "http://localhost:8080")
        return await resolve_source_target_execute(
            supabase=ctx.supabase,
            collabora=ctx.services.collabora,
            source_file_id=file_id,
            source_value=value,
            source_document_kind=document_kind,
            source_page=page,
            collabora_base_url=collabora_url,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Source target resolution failed: {exc}"
        )


def _contains_file_reference(value: Any, file_id: str) -> bool:
    if isinstance(value, str):
        return value == file_id
    if isinstance(value, dict):
        return any(_contains_file_reference(item, file_id) for item in value.values())
    if isinstance(value, list):
        return any(_contains_file_reference(item, file_id) for item in value)
    return False


def _referencing_imports(
    *, ctx: AppContext, file_id: str, shop_domain: str
) -> list[dict[str, str]]:
    from application.use_cases.drafts.list_product_drafts import execute as list_drafts
    from application.use_cases.submitted.list_submitted_documents import (
        execute as list_submitted,
    )

    references: list[dict[str, str]] = []
    collections = (
        ("draft", list_drafts(supabase=ctx.supabase, limit=5000, offset=0, shop_domain=shop_domain)),
        ("submitted", list_submitted(supabase=ctx.supabase, limit=5000, offset=0, shop_domain=shop_domain)),
    )
    for kind, rows in collections:
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("input_file_id") == file_id or _contains_file_reference(row.get("products"), file_id):
                identifier = str(row.get("draft_id") or row.get("submitted_id") or row.get("id") or "")
                references.append({"kind": kind, "id": identifier, "name": str(row.get("draft_name") or row.get("name") or identifier)})
    return references


@router.delete("/files/{file_id}", summary="Delete an uploaded file")
async def delete_file_route(
    file_id: str, request: Request, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:  # rename to avoid shadowing helper
    """Delete an uploaded file from storage."""
    from application.use_cases.files.delete_file import execute as delete_file_execute

    tenant = require_shop_domain(request)
    references = _referencing_imports(ctx=ctx, file_id=file_id, shop_domain=tenant)
    if references:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SOURCE_FILE_IN_USE",
                "reference_count": len(references),
                "affected_imports": references[:20],
            },
        )

    if not delete_file_execute(supabase=ctx.supabase, file_id=file_id):
        raise HTTPException(status_code=404, detail="File not found")

    return {"status": "deleted", "file_id": file_id}


@router.post("/files/bulk-delete", summary="Bulk delete uploaded files")
async def bulk_delete_files(
    payload: BulkDeletePayload, request: Request, ctx: AppContext = Depends(get_ctx)
) -> BulkDeleteResult:
    from application.use_cases.files.bulk_delete_files import (
        execute as bulk_delete_files_execute,
    )

    tenant = require_shop_domain(request)
    protected_ids = [
        file_id
        for file_id in payload.ids
        if _referencing_imports(ctx=ctx, file_id=file_id, shop_domain=tenant)
    ]
    deletable_ids = [file_id for file_id in payload.ids if file_id not in protected_ids]
    result = bulk_delete_files_execute(supabase=ctx.supabase, ids=deletable_ids)
    result["failed_ids"] = [*result["failed_ids"], *protected_ids]
    result["protected_ids"] = protected_ids
    return BulkDeleteResult(**result)


@router.get("/preview/{file_id}", summary="Get PNG preview of file")
async def get_file_preview(
    file_id: str, ctx: AppContext = Depends(get_ctx)
) -> Response:
    """Convert the uploaded file to PNG and return it for preview."""
    from application.use_cases.files.get_file import execute as get_file_execute
    from application.use_cases.files.get_file_thumbnail import (
        execute as get_file_thumb_execute,
    )

    file_entry = get_file_execute(supabase=ctx.supabase, file_id=file_id)
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")
    file_content = file_entry.get("content")
    if not isinstance(file_content, (bytes, bytearray)):
        raise HTTPException(status_code=500, detail="Stored file content is invalid")
    file_name = _required_str(file_entry, "name")
    file_content_type = _required_str(file_entry, "content_type")

    thumbnail_bytes = get_file_thumb_execute(
        supabase=ctx.supabase, file_id=file_id
    )
    if thumbnail_bytes:
        return Response(content=thumbnail_bytes, media_type="image/png")

    try:
        from services.collabora_service import CollaboraUnavailable
        try:
            ctx.services.collabora.readiness()
        except CollaboraUnavailable as exc:
            return JSONResponse(status_code=503, content={"code": exc.code, "detail": str(exc)}, headers={"Cache-Control": "no-store"})
        collabora_url = os.getenv("COLLABORA_URL", "http://localhost:8080")
        preview_png = await generate_thumbnail_bytes(
            file_bytes=bytes(file_content),
            filename=file_name,
            content_type=file_content_type,
            collabora_url=collabora_url,
            collabora=ctx.services.collabora,
        )
        from application.use_cases.files.save_file_thumbnail import (
            execute as save_thumb_execute,
        )

        save_thumb_execute(
            supabase=ctx.supabase, file_id=file_id, content=preview_png
        )

        return Response(content=preview_png, media_type="image/png")
    except Exception as exc:
        message = str(exc).strip() or type(exc).__name__
        code = "COLLABORA_TIMEOUT" if "timeout" in message.lower() else "COLLABORA_UNAVAILABLE"
        return JSONResponse(status_code=503, content={"code": code, "detail": f"Preview generation failed: {message}"}, headers={"Cache-Control": "no-store"})
