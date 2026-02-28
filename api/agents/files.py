"""Agent file upload and processing routes (under `/agents/*`)."""

import json
import logging
import os
import uuid
from datetime import datetime, timezone


from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from app_context import AppContext, get_ctx
from typing import Any
from application.services.document_formats import (
    classify_document,
    supported_extensions_display,
)
from .files_helper import generate_thumbnail_bytes
from .schemas import BulkDeletePayload, BulkDeleteResult

router = APIRouter()
LOG = logging.getLogger(__name__)
MERCHANT_UPLOAD_FILE_ORIGIN = "merchant_upload"


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
    resolved_draft_name = _resolved_draft_name(
        draft_id=draft_id,
        existing_draft_name=existing_draft_name,
        fallback_name=fallback_name,
        existing_input_filename=existing_input_filename,
        fallback_input_filename=fallback_input_filename,
    )

    save_draft_execute(
        supabase=ctx.supabase,
        draft_id=draft_id,
        run_id=existing_run_id or fallback_run_id,
        import_mode=existing_import_mode or fallback_import_mode,
        draft_name=resolved_draft_name,
        shop_domain=existing_shop_domain or shop_domain,
        input_file_id=existing_input_file_id or fallback_input_file_id,
        input_filename=existing_input_filename or fallback_input_filename,
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


@router.get("/files", summary="List uploaded files")
async def list_uploaded_files(
    limit: int = 1000, offset: int = 0, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    """List all uploaded files."""
    from application.use_cases.files.list_files import execute as list_files_execute

    resolved_limit = max(1, min(limit, 5000))
    resolved_offset = max(0, offset)
    files = list_files_execute(
        supabase=ctx.supabase, limit=resolved_limit, offset=resolved_offset
    )
    return {"files": files}


@router.get("/collabora-url", summary="Get current Collabora URL")
async def get_collabora_url(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Get the current Collabora URL and WOPI base URL for interactive viewer."""
    from application.use_cases.collabora.get_collabora_url import (
        execute as get_collabora_execute,
    )

    return get_collabora_execute(collabora=ctx.services.collabora)


@router.post("/upload", summary="Upload a file for preview and processing")
async def upload_file(
    file: UploadFile = File(...), ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    """Upload a file and return a file_id for WOPI preview."""
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
            file_bytes = (
                await ctx.services.collabora.convert_document_to_xlsx_collabora(
                    file_bytes,
                    filename=original_name,
                    content_type=original_content_type or "application/octet-stream",
                    collabora_base_url=collabora_url,
                )
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

    from application.use_cases.files.save_file import execute as save_file_execute

    save_file_execute(
        supabase=ctx.supabase,
        file_id=file_id,
        name=stored_name,
        content=file_bytes,
        content_type=stored_content_type,
        file_origin=MERCHANT_UPLOAD_FILE_ORIGIN,
    )
    thumbnail_generated = False

    from application.use_cases.files.get_file import execute as get_file_execute

    file_name_dict = get_file_execute(supabase=ctx.supabase, file_id=file_id)
    file_name = _optional_str(file_name_dict, "name") or "unknown"

    return {
        "file_id": file_id,
        "filename": file_name,
        "size": len(file_bytes),
        "thumbnail_generated": thumbnail_generated,
    }


@router.post("/excel", summary="Process an Excel file through the AI agent workflow")
@router.post("/import", summary="Process a document through the AI agent workflow")
async def process_excel(
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
        if not file_id:
            raise HTTPException(
                status_code=400,
                detail="Offloaded imports require file_id from /agents/upload",
            )
        active_draft = _find_active_import_draft_for_file(
            ctx=ctx,
            file_id=file_id,
            shop_domain=shop_domain,
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
                    shop_domain=shop_domain,
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
                shop_domain=shop_domain,
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
                    "shop_domain": shop_domain,
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
                    "shop_domain": shop_domain,
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
        shop_domain=shop_domain,
    )

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
    sheet: str | None = Form(None),
    cell: str | None = Form(None),
    cell_range: str | None = Form(None),
    source_refs_json: str | None = Form(None),
    preferred_sheet: str | None = Form(None),
    highlight_file_id: str | None = Form(None),
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

    try:
        return await create_source_highlight_execute(
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
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Source highlight generation failed: {exc}"
        )


@router.get(
    "/files/{file_id}/source-target",
    summary="Resolve non-spreadsheet Collabora source target",
)
async def resolve_source_target(
    file_id: str,
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


@router.delete("/files/{file_id}", summary="Delete an uploaded file")
async def delete_file_route(
    file_id: str, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:  # rename to avoid shadowing helper
    """Delete an uploaded file from storage."""
    from application.use_cases.files.delete_file import execute as delete_file_execute

    if not delete_file_execute(supabase=ctx.supabase, file_id=file_id):
        raise HTTPException(status_code=404, detail="File not found")

    return {"status": "deleted", "file_id": file_id}


@router.post("/files/bulk-delete", summary="Bulk delete uploaded files")
async def bulk_delete_files(
    payload: BulkDeletePayload, ctx: AppContext = Depends(get_ctx)
) -> BulkDeleteResult:
    from application.use_cases.files.bulk_delete_files import (
        execute as bulk_delete_files_execute,
    )

    result = bulk_delete_files_execute(supabase=ctx.supabase, ids=payload.ids)
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
        raise HTTPException(status_code=500, detail=f"Preview generation failed: {exc}")
