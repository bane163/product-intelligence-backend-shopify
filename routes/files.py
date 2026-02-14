"""Agent file upload and processing routes (under `/agents/*`).

Endpoints moved from `agents.py` that deal with uploads, Excel processing,
previews, and file metadata.
"""

import os
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import Response

from ai.excel_workflow import run_excel_agent_workflow
from ._storage import save_file, get_file, delete_file, list_files

router = APIRouter(tags=["agents"])


@router.get("/files", summary="List uploaded files")
async def list_uploaded_files(limit: int = 100, offset: int = 0) -> dict:
    """List all uploaded files."""
    files = list_files(limit=limit, offset=offset)
    return {"files": files}


@router.get("/collabora-url", summary="Get current Collabora URL")
async def get_collabora_url() -> dict:
    """Get the current Collabora URL and WOPI base URL for interactive viewer."""
    from cloudflare_tunnel import get_tunnel_url

    tunnel_url = get_tunnel_url()
    fallback_url = os.getenv("COLLABORA_URL", "http://localhost:9980")

    # WOPI base URL - accessible from Collabora container via Docker network
    wopi_host = os.getenv("WOPI_HOST", "shopify-backend")
    wopi_port = os.getenv("WOPI_PORT", "8000")
    wopi_base_url = f"http://{wopi_host}:{wopi_port}/agents/wopi/files"

    return {
        "collabora_url": tunnel_url or fallback_url,
        "wopi_base_url": wopi_base_url,
        "is_tunnel": tunnel_url is not None,
    }


@router.post("/upload", summary="Upload a file for preview and processing")
async def upload_file(file: UploadFile = File(...)) -> dict:
    """Upload a file and return a file_id for WOPI preview."""
    file_bytes = await file.read()
    file_id = str(uuid.uuid4())

    save_file(
        file_id,
        name=file.filename or "document.xlsx",
        content=file_bytes,
        content_type=file.content_type
        or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    file_name_dict = get_file(file_id)
    file_name = file_name_dict["name"] if file_name_dict else "unknown"

    return {
        "file_id": file_id,
        "filename": file_name,
        "size": len(file_bytes),
    }


@router.post("/excel", summary="Process an Excel file through the AI agent workflow")
async def process_excel(
    file_id: str = Form(None),
    file: UploadFile = File(None),
    prompt: str = Form("Please analyze the spreadsheet and the associated image(s)."),
    collabora_url: str | None = Form(None),
    write_to_file: bool = Form(False),
    output_path: str | None = Form(None),
    writer_prompt: str | None = Form(None),
) -> dict:
    """Process an Excel file through the AI agent workflow.

    Accepts either file_id or direct upload.
    """
    # Get file bytes from either file_id or direct upload
    if file_id:
        file_entry = get_file(file_id)
        if not file_entry:
            raise HTTPException(status_code=404, detail="File not found")
        file_bytes = file_entry["content"]
    elif file:
        file_bytes = await file.read()
    else:
        raise HTTPException(
            status_code=400,
            detail="Either file_id (from /agents/upload) or file upload is required",
        )

    try:
        result = await run_excel_agent_workflow(
            file_bytes,
            collabora_base_url=collabora_url,
            agent_prompt=prompt,
            write_to_file=write_to_file,
            output_path=output_path,
            writer_agent_prompt=writer_prompt,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")

    # If the workflow wrote an output workbook to disk, save it to storage so the
    # Collabora viewer can open it via the existing WOPI flow. Return the new
    # file_id and filename in the response so the frontend can show the viewer.
    generated_file_id = None
    generated_filename = None
    if write_to_file and isinstance(result, str):
        try:
            if os.path.exists(result):
                with open(result, "rb") as fh:
                    out_bytes = fh.read()
                generated_file_id = str(uuid.uuid4())
                generated_filename = os.path.basename(result)
                # Determine content type based on extension (CSV vs XLSX)
                ct = (
                    "text/csv"
                    if generated_filename.lower().endswith(".csv")
                    else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                save_file(
                    generated_file_id,
                    name=generated_filename,
                    content=out_bytes,
                    content_type=ct,
                )
                # Remove the local file after uploading so nothing is persisted locally
                try:
                    os.remove(result)
                except Exception:
                    pass
                # Replace result with metadata so callers can easily access viewer
                result = {
                    "workbook_path": result,
                    "file_id": generated_file_id,
                    "filename": generated_filename,
                }
        except Exception:
            LOG = None  # avoid lint error if LOG not used; ignore storage failures

    # Optionally clean up the stored file after processing
    if file_id:
        delete_file(file_id)

    return {"result": result}


@router.get("/files/{file_id}", summary="Get file info")
async def get_file_info(file_id: str) -> dict:
    """Get information about an uploaded file."""
    file_entry = get_file(file_id)
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")

    return {
        "file_id": file_id,
        "filename": file_entry["name"],
        "size": len(file_entry["content"]),
        "content_type": file_entry["content_type"],
        "storage_path": file_entry["storage_path"],
    }


@router.delete("/files/{file_id}", summary="Delete an uploaded file")
async def delete_file_route(file_id: str) -> dict:  # rename to avoid shadowing helper
    """Delete an uploaded file from storage."""
    if not delete_file(file_id):
        raise HTTPException(status_code=404, detail="File not found")

    return {"status": "deleted", "file_id": file_id}


@router.get("/preview/{file_id}", summary="Get PNG preview of file")
async def get_file_preview(file_id: str) -> Response:
    """Convert the uploaded file to PNG and return it for preview."""
    from ai.collabora_utils import (
        convert_excel_to_pdf_collabora,
        convert_pdf_to_png_collabora,
    )
    from cloudflare_tunnel import get_tunnel_url

    file_entry = get_file(file_id)
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")

    # Check if we already have a cached preview
    if "preview_png" in file_entry:
        return Response(content=file_entry["preview_png"], media_type="image/png")

    try:
        collabora_url = get_tunnel_url() or os.getenv(
            "COLLABORA_URL", "http://localhost:9980"
        )

        # Convert Excel to PDF
        pdf_bytes = await convert_excel_to_pdf_collabora(
            file_entry["content"], collabora_base_url=collabora_url
        )

        # Convert PDF to PNG
        png_list = await convert_pdf_to_png_collabora(
            pdf_bytes, collabora_base_url=collabora_url
        )

        if not png_list:
            raise HTTPException(status_code=500, detail="Failed to generate preview")

        # Use the first page as the preview
        preview_png = png_list[0]

        # Cache the preview
        file_entry["preview_png"] = preview_png

        return Response(content=preview_png, media_type="image/png")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Preview generation failed: {exc}")
