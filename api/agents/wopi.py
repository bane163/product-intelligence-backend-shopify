"""WOPI endpoints for Collabora Online integration."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app_context import AppContext, get_ctx

router = APIRouter()


@router.get("/wopi/files/{file_id}", summary="WOPI CheckFileInfo")
async def wopi_check_file_info(file_id: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    """WOPI CheckFileInfo endpoint.

    Returns metadata about the file for Collabora Online.
    """
    file_data = ctx.services.supabase.get_file(file_id)
    if not file_data:
        raise HTTPException(status_code=404, detail="File not found")

    return {
        "BaseFileName": file_data["name"],
        "Size": len(file_data["content"]),
        "OwnerId": "shopify_user",
        "UserId": "shopify_user",
        "UserFriendlyName": "Shopify User",
        "Version": "1.0",
        "SupportsUpdate": False,
        "SupportsLocks": False,
        "UserCanWrite": False,
        "UserCanNotWriteRelative": True,
    }


@router.get("/wopi/files/{file_id}/contents", summary="WOPI GetFile")
async def wopi_get_file(file_id: str, ctx: AppContext = Depends(get_ctx)) -> Response:
    """WOPI GetFile endpoint.

    Returns the file contents for Collabora Online to display.
    """
    file_data = ctx.services.supabase.get_file(file_id)
    if not file_data:
        raise HTTPException(status_code=404, detail="File not found")

    return Response(
        content=file_data["content"],
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{file_data["name"]}"'},
    )
