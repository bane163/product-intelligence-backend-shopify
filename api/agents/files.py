"""Agent file upload and processing routes (under `/agents/*`)."""

import json
import logging
import os
import uuid


from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

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


@router.get("/files", summary="List uploaded files")
async def list_uploaded_files(
    limit: int = 100, offset: int = 0, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    """List all uploaded files."""
    from application.use_cases.files.list_files import execute as list_files_execute

    files = list_files_execute(
        supabase=ctx.services.supabase, limit=limit, offset=offset
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
        supabase=ctx.services.supabase,
        file_id=file_id,
        name=stored_name,
        content=file_bytes,
        content_type=stored_content_type,
    )
    thumbnail_generated = False

    from application.use_cases.files.get_file import execute as get_file_execute

    file_name_dict = get_file_execute(supabase=ctx.services.supabase, file_id=file_id)
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

        file_entry = get_file_execute(supabase=ctx.services.supabase, file_id=file_id)
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

    result = await process_document_execute(
        supabase=ctx.services.supabase,
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

    file_entry = get_file_execute(supabase=ctx.services.supabase, file_id=file_id)
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
            supabase=ctx.services.supabase,
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
            supabase=ctx.services.supabase,
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

    if not delete_file_execute(supabase=ctx.services.supabase, file_id=file_id):
        raise HTTPException(status_code=404, detail="File not found")

    return {"status": "deleted", "file_id": file_id}


@router.post("/files/bulk-delete", summary="Bulk delete uploaded files")
async def bulk_delete_files(
    payload: BulkDeletePayload, ctx: AppContext = Depends(get_ctx)
) -> BulkDeleteResult:
    from application.use_cases.files.bulk_delete_files import (
        execute as bulk_delete_files_execute,
    )

    result = bulk_delete_files_execute(supabase=ctx.services.supabase, ids=payload.ids)
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

    file_entry = get_file_execute(supabase=ctx.services.supabase, file_id=file_id)
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")
    file_content = file_entry.get("content")
    if not isinstance(file_content, (bytes, bytearray)):
        raise HTTPException(status_code=500, detail="Stored file content is invalid")
    file_name = _required_str(file_entry, "name")
    file_content_type = _required_str(file_entry, "content_type")

    thumbnail_bytes = get_file_thumb_execute(
        supabase=ctx.services.supabase, file_id=file_id
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
            supabase=ctx.services.supabase, file_id=file_id, content=preview_png
        )

        return Response(content=preview_png, media_type="image/png")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Preview generation failed: {exc}")
