"""Agent file upload and processing routes (under `/agents/*`)."""

import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app_context import AppContext, get_ctx
from typing import Any
from .files_helper import generate_thumbnail_bytes
from .schemas import BulkDeletePayload, BulkDeleteResult

router = APIRouter()
LOG = logging.getLogger(__name__)


@router.get("/files", summary="List uploaded files")
async def list_uploaded_files(
    limit: int = 100, offset: int = 0, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    """List all uploaded files."""
    from application.use_cases.list_files import execute as list_files_execute
    files = list_files_execute(supabase=ctx.services.supabase, limit=limit, offset=offset)
    return {"files": files}


@router.get("/collabora-url", summary="Get current Collabora URL")
async def get_collabora_url(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Get the current Collabora URL and WOPI base URL for interactive viewer."""
    from application.use_cases.get_collabora_url import execute as get_collabora_execute
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
    is_csv = (
        original_name.lower().endswith(".csv")
        or original_content_type.lower() == "text/csv"
    )

    stored_name = original_name
    stored_content_type = (
        file.content_type
        or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    if is_csv:
        collabora_url = os.getenv("COLLABORA_URL", "http://localhost:9980")
        try:
            from application.use_cases.convert_csv_to_excel import execute as convert_csv_execute
            file_bytes = await convert_csv_execute(collabora=ctx.services.collabora, csv_bytes=file_bytes, collabora_base_url=collabora_url)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"CSV conversion failed: {exc}")
        base, _ = os.path.splitext(original_name)
        stored_name = f"{base or 'document'}.xlsx"
        stored_content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    from application.use_cases.save_file import execute as save_file_execute
    save_file_execute(supabase=ctx.services.supabase, file_id=file_id, name=stored_name, content=file_bytes, content_type=stored_content_type)
    thumbnail_generated = False
    try:
        collabora_url = os.getenv("COLLABORA_URL", "http://localhost:8080")
        thumbnail_bytes = await generate_thumbnail_bytes(
            file_bytes=file_bytes,
            filename=stored_name,
            content_type=stored_content_type,
            collabora_url=collabora_url,
            collabora=ctx.services.collabora,
        )
        from application.use_cases.save_file_thumbnail import execute as save_thumb_execute
        thumbnail_path = save_thumb_execute(supabase=ctx.services.supabase, file_id=file_id, content=thumbnail_bytes)
        thumbnail_generated = bool(thumbnail_path)
    except Exception as e:
        LOG.warning(
            "Thumbnail generation failed for file_id=%s: %s", file_id, e, exc_info=True
        )

    from application.use_cases.get_file import execute as get_file_execute
    file_name_dict = get_file_execute(supabase=ctx.services.supabase, file_id=file_id)
    file_name = file_name_dict["name"] if file_name_dict else "unknown"

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
    file_id: str = Form(None),
    file: UploadFile = File(None),
    prompt: str = Form("Please analyze the document and the associated image(s)."),
    collabora_url: str | None = Form(None),
    write_to_file: bool = Form(False),
    output_path: str | None = Form(None),
    writer_prompt: str | None = Form(None),
    shop_domain: str | None = Form(None),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    """Thin controller delegating to application.use_cases.process_document.execute.

    Accepts either file_id or direct upload.
    """
    from application.use_cases.process_document import execute as process_document_execute

    # Resolve file bytes and metadata
    file_entry = None
    file_bytes_data = None
    if file_id:
        from application.use_cases.get_file import execute as get_file_execute
        file_entry = get_file_execute(supabase=ctx.services.supabase, file_id=file_id)
        if not file_entry:
            raise HTTPException(status_code=404, detail="File not found")
        file_bytes_data = file_entry.get("content")
    elif file:
        file_bytes_data = await file.read()
    else:
        raise HTTPException(
            status_code=400,
            detail="Either file_id (from /agents/upload) or file upload is required",
        )

    result = await process_document_execute(
        supabase=ctx.services.supabase,
        llm=ctx.services.llm,
        tracing=ctx.services.tracing,
        ctx=ctx,
        file_bytes=file_bytes_data,
        input_name=(file.filename if file else None) or (file_entry.get("name") if file_entry else None),
        input_content_type=(file.content_type if file else None) or (file_entry.get("content_type") if file_entry else None),
        run_id=run_id,
        prompt=prompt,
        collabora_url=collabora_url,
        write_to_file=write_to_file,
        output_path=output_path,
        writer_prompt=writer_prompt,
        shop_domain=shop_domain,
    )

    return result



@router.get("/files/{file_id}", summary="Get file info")
async def get_file_info(file_id: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Get information about an uploaded file."""
    from application.use_cases.get_file import execute as get_file_execute
    file_entry = get_file_execute(supabase=ctx.services.supabase, file_id=file_id)
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")

    return {
        "file_id": file_id,
        "filename": file_entry["name"],
        "size": len(file_entry["content"]),
        "content_type": file_entry["content_type"],
        "storage_path": file_entry["storage_path"],
        "thumbnail_storage_path": file_entry.get("thumbnail_storage_path"),
    }


@router.delete("/files/{file_id}", summary="Delete an uploaded file")
async def delete_file_route(
    file_id: str, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:  # rename to avoid shadowing helper
    """Delete an uploaded file from storage."""
    from application.use_cases.delete_file import execute as delete_file_execute
    if not delete_file_execute(supabase=ctx.services.supabase, file_id=file_id):
        raise HTTPException(status_code=404, detail="File not found")

    return {"status": "deleted", "file_id": file_id}


@router.post("/files/bulk-delete", summary="Bulk delete uploaded files")
async def bulk_delete_files(
    payload: BulkDeletePayload, ctx: AppContext = Depends(get_ctx)
) -> BulkDeleteResult:
    from application.use_cases.bulk_delete_files import execute as bulk_delete_files_execute

    result = bulk_delete_files_execute(supabase=ctx.services.supabase, ids=payload.ids)
    return BulkDeleteResult(**result)


@router.get("/preview/{file_id}", summary="Get PNG preview of file")
async def get_file_preview(
    file_id: str, ctx: AppContext = Depends(get_ctx)
) -> Response:
    """Convert the uploaded file to PNG and return it for preview."""
    from application.use_cases.get_file import execute as get_file_execute
    from application.use_cases.get_file_thumbnail import execute as get_file_thumb_execute
    file_entry = get_file_execute(supabase=ctx.services.supabase, file_id=file_id)
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")

    thumbnail_bytes = get_file_thumb_execute(supabase=ctx.services.supabase, file_id=file_id)
    if thumbnail_bytes:
        return Response(content=thumbnail_bytes, media_type="image/png")

    try:
        collabora_url = os.getenv("COLLABORA_URL", "http://localhost:8080")
        preview_png = await generate_thumbnail_bytes(
            file_bytes=file_entry["content"],
            filename=file_entry["name"],
            content_type=file_entry["content_type"],
            collabora_url=collabora_url,
            ctx=ctx,
        )
        from application.use_cases.save_file_thumbnail import execute as save_thumb_execute
        save_thumb_execute(supabase=ctx.services.supabase, file_id=file_id, content=preview_png)

        return Response(content=preview_png, media_type="image/png")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Preview generation failed: {exc}")
