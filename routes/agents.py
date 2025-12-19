"""
Agents API routes for Excel file processing with Collabora preview.

Provides:
- POST /agents/upload    - Upload a file and get file_id for WOPI preview
- POST /agents/excel     - Process a file through the AI agent workflow
- GET  /agents/wopi/files/{file_id}          - WOPI CheckFileInfo
- GET  /agents/wopi/files/{file_id}/contents - WOPI GetFile
"""

import os
import uuid
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import Response

from ai.excel_workflow import run_excel_agent_workflow


router = APIRouter(prefix="/agents", tags=["agents"])

# In-memory file storage for uploaded files pending processing
# In production, use a proper file store (S3, database, etc.)
file_storage: Dict[str, Dict[str, Any]] = {}


@router.get("/collabora-url", summary="Get current Collabora URL")
async def get_collabora_url() -> dict:
    """Get the current Collabora URL and WOPI base URL for interactive viewer."""
    from cloudflare_tunnel import get_tunnel_url
    import os
    
    tunnel_url = get_tunnel_url()
    fallback_url = os.getenv("COLLABORA_URL", "http://localhost:9980")
    
    # WOPI base URL - accessible from Collabora container via Docker network
    # In Docker: use container name. Locally: use localhost.
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
    """Upload a file and return a file_id for WOPI preview.
    
    The file is stored in memory until it's processed or cleaned up.
    Returns the file_id which can be used to:
    - View the file in Collabora via WOPI endpoints
    - Process the file through /agents/excel
    """
    file_bytes = await file.read()
    file_id = str(uuid.uuid4())
    
    file_storage[file_id] = {
        "name": file.filename or "document.xlsx",
        "content": file_bytes,
        "content_type": file.content_type or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    
    return {
        "file_id": file_id,
        "filename": file_storage[file_id]["name"],
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
    
    Accepts either:
    - file_id: ID of a previously uploaded file (from /agents/upload)
    - file: Direct file upload
    
    Returns the agent result (ProductsList or workbook path).
    """
    # Get file bytes from either file_id or direct upload
    if file_id and file_id in file_storage:
        file_bytes = file_storage[file_id]["content"]
    elif file:
        file_bytes = await file.read()
    else:
        raise HTTPException(
            status_code=400,
            detail="Either file_id (from /agents/upload) or file upload is required"
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
    
    # Optionally clean up the stored file after processing
    if file_id and file_id in file_storage:
        del file_storage[file_id]
    
    return {"result": result}


# ============================================================================
# WOPI Endpoints for Collabora Online integration
# ============================================================================

@router.get("/wopi/files/{file_id}", summary="WOPI CheckFileInfo")
async def wopi_check_file_info(file_id: str) -> dict:
    """WOPI CheckFileInfo endpoint.
    
    Returns metadata about the file for Collabora Online.
    """
    if file_id not in file_storage:
        raise HTTPException(status_code=404, detail="File not found")
    
    file_data = file_storage[file_id]
    
    return {
        "BaseFileName": file_data["name"],
        "Size": len(file_data["content"]),
        "OwnerId": "shopify_user",
        "UserId": "shopify_user",
        "UserFriendlyName": "Shopify User",
        "Version": "1.0",
        "SupportsUpdate": False,  # Read-only preview
        "SupportsLocks": False,
        "UserCanWrite": False,
        "UserCanNotWriteRelative": True,
    }


@router.get("/wopi/files/{file_id}/contents", summary="WOPI GetFile")
async def wopi_get_file(file_id: str) -> Response:
    """WOPI GetFile endpoint.
    
    Returns the file contents for Collabora Online to display.
    """
    if file_id not in file_storage:
        raise HTTPException(status_code=404, detail="File not found")
    
    file_data = file_storage[file_id]
    
    return Response(
        content=file_data["content"],
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{file_data["name"]}"',
        }
    )


@router.get("/files/{file_id}", summary="Get file info")
async def get_file_info(file_id: str) -> dict:
    """Get information about an uploaded file."""
    if file_id not in file_storage:
        raise HTTPException(status_code=404, detail="File not found")
    
    file_data = file_storage[file_id]
    
    return {
        "file_id": file_id,
        "filename": file_data["name"],
        "size": len(file_data["content"]),
        "content_type": file_data["content_type"],
    }


@router.delete("/files/{file_id}", summary="Delete an uploaded file")
async def delete_file(file_id: str) -> dict:
    """Delete an uploaded file from storage."""
    if file_id not in file_storage:
        raise HTTPException(status_code=404, detail="File not found")
    
    del file_storage[file_id]
    
    return {"status": "deleted", "file_id": file_id}


@router.get("/preview/{file_id}", summary="Get PNG preview of file")
async def get_file_preview(file_id: str) -> Response:
    """Convert the uploaded file to PNG and return it for preview.
    
    Uses Collabora's conversion API to convert Excel -> PDF -> PNG.
    """
    from ai.collabora_utils import (
        convert_excel_to_pdf_collabora,
        convert_pdf_to_png_collabora,
    )
    import os
    
    if file_id not in file_storage:
        raise HTTPException(status_code=404, detail="File not found")
    
    file_data = file_storage[file_id]
    file_bytes = file_data["content"]
    
    # Check if we already have a cached preview
    if "preview_png" in file_data:
        return Response(
            content=file_data["preview_png"],
            media_type="image/png",
        )
    
    try:
        from cloudflare_tunnel import get_tunnel_url
        collabora_url = get_tunnel_url() or os.getenv("COLLABORA_URL", "http://localhost:9980")
        
        # Convert Excel to PDF
        pdf_bytes = await convert_excel_to_pdf_collabora(
            file_bytes, 
            collabora_base_url=collabora_url
        )
        
        # Convert PDF to PNG
        png_list = await convert_pdf_to_png_collabora(
            pdf_bytes,
            collabora_base_url=collabora_url
        )
        
        if not png_list:
            raise HTTPException(status_code=500, detail="Failed to generate preview")
        
        # Use the first page as the preview
        preview_png = png_list[0]
        
        # Cache the preview
        file_data["preview_png"] = preview_png
        
        return Response(
            content=preview_png,
            media_type="image/png",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Preview generation failed: {exc}")

