"""Agent file upload and processing routes (under `/agents/*`)."""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app_context import AppContext, get_ctx
from .schemas import BulkDeletePayload, BulkDeleteResult

router = APIRouter()
LOG = logging.getLogger(__name__)


async def _generate_thumbnail_bytes(
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    collabora_url: str,
    ctx: AppContext,
) -> bytes:
    png_list = await ctx.services.collabora.convert_document_to_png_collabora(
        file_bytes,
        filename=filename,
        content_type=content_type,
        collabora_base_url=collabora_url,
    )
    if not png_list:
        raise RuntimeError("No PNG pages returned by Collabora convert-to")
    return png_list[0]


@router.get("/files", summary="List uploaded files")
async def list_uploaded_files(
    limit: int = 100, offset: int = 0, ctx: AppContext = Depends(get_ctx)
) -> dict:
    """List all uploaded files."""
    files = ctx.services.supabase.list_files(limit=limit, offset=offset)
    return {"files": files}


@router.get("/collabora-url", summary="Get current Collabora URL")
async def get_collabora_url(ctx: AppContext = Depends(get_ctx)) -> dict:
    """Get the current Collabora URL and WOPI base URL for interactive viewer."""
    return ctx.services.collabora.get_collabora_url_payload()


@router.post("/upload", summary="Upload a file for preview and processing")
async def upload_file(
    file: UploadFile = File(...), ctx: AppContext = Depends(get_ctx)
) -> dict:
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
            file_bytes = await ctx.services.collabora.convert_csv_to_excel(
                file_bytes, collabora_base_url=collabora_url
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"CSV conversion failed: {exc}")
        base, _ = os.path.splitext(original_name)
        stored_name = f"{base or 'document'}.xlsx"
        stored_content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    ctx.services.supabase.save_file(
        file_id,
        name=stored_name,
        content=file_bytes,
        content_type=stored_content_type,
    )
    thumbnail_generated = False
    try:
        collabora_url = os.getenv("COLLABORA_URL", "http://localhost:8080")
        thumbnail_bytes = await _generate_thumbnail_bytes(
            file_bytes=file_bytes,
            filename=stored_name,
            content_type=stored_content_type,
            collabora_url=collabora_url,
            ctx=ctx,
        )
        thumbnail_path = ctx.services.supabase.save_file_thumbnail(
            file_id=file_id, content=thumbnail_bytes
        )
        thumbnail_generated = bool(thumbnail_path)
    except Exception as e:
        LOG.warning(
            "Thumbnail generation failed for file_id=%s: %s", file_id, e, exc_info=True
        )

    file_name_dict = ctx.services.supabase.get_file(file_id)
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
) -> dict:
    """Process a document through the AI agent workflow.

    Accepts either file_id or direct upload.
    """
    run_id = run_id or str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    event_seq = 0
    message_seq = 0
    usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def emit_and_persist(
        *,
        phase: str,
        message: str,
        level: str = "info",
        payload_preview: Any = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        nonlocal event_seq
        event_seq += 1
        event = ctx.services.tracing.emit_run_event(
            run_id,
            phase=phase,
            message=message,
            level=level,
            payload_preview=payload_preview,
            error=error,
            metadata=metadata,
        )
        ctx.services.supabase.append_run_event(run_id, event, event_seq)
        return event

    def trace_event(**kwargs):
        nonlocal message_seq
        event = emit_and_persist(
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
            message_seq += 1
            ctx.services.supabase.append_run_message(
                run_id,
                role=transcript_role,
                message=transcript_text,
                seq=message_seq,
                meta=kwargs.get("transcript_meta"),
            )
        metadata = kwargs.get("metadata") or {}
        usage = metadata.get("usage") if isinstance(metadata, dict) else None
        if isinstance(usage, dict):
            usage_totals["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            usage_totals["completion_tokens"] += int(
                usage.get("completion_tokens") or 0
            )
            usage_totals["total_tokens"] += int(usage.get("total_tokens") or 0)
            ctx.services.supabase.create_or_update_run(
                run_id,
                {
                    "prompt_tokens": usage_totals["prompt_tokens"],
                    "completion_tokens": usage_totals["completion_tokens"],
                    "total_tokens": usage_totals["total_tokens"],
                },
            )
        if isinstance(metadata, dict) and metadata.get("model_name"):
            ctx.services.supabase.create_or_update_run(
                run_id,
                {
                    "model_name": metadata.get("model_name"),
                    "provider": metadata.get("provider"),
                },
            )
        return event

    ctx.services.supabase.create_or_update_run(
        run_id,
        {
            "status": "running",
            "source": "document_import",
            "started_at": started_at.isoformat(),
            "prompt": prompt,
            "writer_prompt": writer_prompt,
        },
    )
    emit_and_persist(
        phase="request_received",
        message="Received /agents/import request",
        payload_preview={"write_to_file": write_to_file, "has_file_id": bool(file_id)},
    )

    # Get file bytes from either file_id or direct upload
    input_name: str | None = None
    input_content_type: str | None = None
    if file_id:
        file_entry = ctx.services.supabase.get_file(file_id)
        if not file_entry:
            raise HTTPException(status_code=404, detail="File not found")
        file_bytes = file_entry["content"]
        input_name = file_entry.get("name")
        input_content_type = file_entry.get("content_type")
    elif file:
        file_bytes = await file.read()
        input_name = file.filename
        input_content_type = file.content_type
    else:
        raise HTTPException(
            status_code=400,
            detail="Either file_id (from /agents/upload) or file upload is required",
        )
    ctx.services.supabase.create_or_update_run(
        run_id,
        {
            "input_file_id": file_id,
            "input_filename": input_name,
            "input_content_type": input_content_type,
            "input_size_bytes": len(file_bytes),
        },
    )

    model_env: dict[str, str] | None = None
    if shop_domain:
        active_model = ctx.services.supabase.get_active_llm_model_config(shop_domain)
        if active_model:
            model_env = {
                "OLLAMA_CLOUD_URL": str(active_model.get("base_url") or ""),
                "OLLAMA_MODEL_ID": str(active_model.get("model_id") or ""),
                "OLLAMA_API_KEY": str(active_model.get("api_key") or ""),
            }
            ctx.services.supabase.create_or_update_run(
                run_id,
                {
                    "model_name": active_model.get("model_id"),
                    "provider": active_model.get("provider"),
                },
            )
            emit_and_persist(
                phase="model_config_selected",
                message="Resolved active LLM config from database",
                payload_preview={
                    "shop_domain": shop_domain,
                    "model_name": active_model.get("model_id"),
                    "provider": active_model.get("provider"),
                    "config_name": active_model.get("name"),
                },
            )

    try:
        emit_and_persist(
            phase="workflow_start",
            message="Starting document workflow execution",
            payload_preview={"input_bytes": len(file_bytes)},
        )
        # Prefer preserving the original filename when asking the writer to
        # produce an output file. Use the input filename if available; otherwise
        # fall back to the provided output_path or a default name.
        final_output_path = None
        if write_to_file:
            try:
                base_name = os.path.basename(input_name) if input_name else None
                if base_name:
                    name, ext = os.path.splitext(base_name)
                    # Append "-products" before the extension, preserving the original extension.
                    suffixed = f"{name}-products{ext or ''}"
                    final_output_path = os.path.abspath(
                        os.path.join(os.getcwd(), suffixed)
                    )
                elif output_path:
                    final_output_path = os.path.abspath(output_path)
                else:
                    final_output_path = os.path.abspath(
                        os.path.join(os.getcwd(), "import-products.xlsx")
                    )
            except Exception:
                final_output_path = output_path

        result = await ctx.services.llm.run_excel_agent_workflow(
            file_bytes,
            collabora_base_url=collabora_url,
            agent_prompt=prompt,
            model_env=model_env,
            write_to_file=write_to_file,
            output_path=final_output_path,
            writer_agent_prompt=writer_prompt,
            trace_event=trace_event,
        )
        emit_and_persist(phase="workflow_done", message="Document workflow completed")
    except RuntimeError as exc:
        emit_and_persist(
            phase="workflow_error",
            message="Document workflow failed",
            level="error",
            error=str(exc),
        )
        duration_ms = int(
            (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
        )
        ctx.services.supabase.finalize_run(
            run_id, status="error", duration_ms=duration_ms, error=str(exc)
        )
        ctx.services.tracing.complete_run(run_id)
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        emit_and_persist(
            phase="workflow_error",
            message="Unexpected workflow error",
            level="error",
            error=str(exc),
        )
        duration_ms = int(
            (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
        )
        ctx.services.supabase.finalize_run(
            run_id, status="error", duration_ms=duration_ms, error=str(exc)
        )
        ctx.services.tracing.complete_run(run_id)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")

    # If the writer already uploaded to storage and returned metadata, accept it
    generated_file_id = None
    generated_filename = None
    if write_to_file:
        if isinstance(result, dict) and result.get("file_id"):
            pass
        elif isinstance(result, str):
            try:
                if os.path.exists(result):
                    with open(result, "rb") as fh:
                        out_bytes = fh.read()
                    generated_file_id = str(uuid.uuid4())
                    generated_filename = os.path.basename(result)
                    ct = (
                        "text/csv"
                        if generated_filename.lower().endswith(".csv")
                        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    ctx.services.supabase.save_file(
                        generated_file_id,
                        name=generated_filename,
                        content=out_bytes,
                        content_type=ct,
                    )
                    try:
                        os.remove(result)
                    except Exception:
                        pass
                    result = {
                        "workbook_path": result,
                        "file_id": generated_file_id,
                        "filename": generated_filename,
                    }
            except Exception as exc:
                emit_and_persist(
                    phase="storage_upload_error",
                    message="Failed to persist generated workbook to storage",
                    level="error",
                    error=str(exc),
                )

    if file_id:
        ctx.services.supabase.delete_file(file_id)
    output_meta = {}
    if isinstance(result, dict):
        output_meta = {
            "output_file_id": result.get("file_id"),
            "output_filename": result.get("filename"),
        }
    duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    ctx.services.supabase.finalize_run(
        run_id, status="success", duration_ms=duration_ms, extra_fields=output_meta
    )
    emit_and_persist(phase="request_done", message="Completed /agents/import request")
    ctx.services.tracing.complete_run(run_id)

    return {"run_id": run_id, "result": result}


@router.get("/files/{file_id}", summary="Get file info")
async def get_file_info(file_id: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Get information about an uploaded file."""
    file_entry = ctx.services.supabase.get_file(file_id)
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
) -> dict:  # rename to avoid shadowing helper
    """Delete an uploaded file from storage."""
    if not ctx.services.supabase.delete_file(file_id):
        raise HTTPException(status_code=404, detail="File not found")

    return {"status": "deleted", "file_id": file_id}


@router.post("/files/bulk-delete", summary="Bulk delete uploaded files")
async def bulk_delete_files(
    payload: BulkDeletePayload, ctx: AppContext = Depends(get_ctx)
) -> BulkDeleteResult:
    deleted_ids: list[str] = []
    failed_ids: list[str] = []
    for file_id in payload.ids:
        if ctx.services.supabase.delete_file(file_id):
            deleted_ids.append(file_id)
        else:
            failed_ids.append(file_id)
    return BulkDeleteResult(deleted_ids=deleted_ids, failed_ids=failed_ids)


@router.get("/preview/{file_id}", summary="Get PNG preview of file")
async def get_file_preview(
    file_id: str, ctx: AppContext = Depends(get_ctx)
) -> Response:
    """Convert the uploaded file to PNG and return it for preview."""
    file_entry = ctx.services.supabase.get_file(file_id)
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")

    thumbnail_bytes = ctx.services.supabase.get_file_thumbnail(file_id)
    if thumbnail_bytes:
        return Response(content=thumbnail_bytes, media_type="image/png")

    try:
        collabora_url = os.getenv("COLLABORA_URL", "http://localhost:8080")
        preview_png = await _generate_thumbnail_bytes(
            file_bytes=file_entry["content"],
            filename=file_entry["name"],
            content_type=file_entry["content_type"],
            collabora_url=collabora_url,
            ctx=ctx,
        )
        ctx.services.supabase.save_file_thumbnail(file_id=file_id, content=preview_png)

        return Response(content=preview_png, media_type="image/png")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Preview generation failed: {exc}")
