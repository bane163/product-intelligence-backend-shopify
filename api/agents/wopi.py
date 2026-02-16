"""WOPI endpoints for Collabora Online integration."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from typing import Any

from app_context import AppContext, get_ctx

router = APIRouter()


def _can_write(request: Request) -> bool:
    if request.query_params.get("access_token") == "edit":
        return True
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer ") and auth.split(" ", 1)[1].strip() == "edit":
        return True
    return False


@router.get("/wopi/files/{file_id}", summary="WOPI CheckFileInfo")
async def wopi_check_file_info(
    file_id: str, request: Request, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    """WOPI CheckFileInfo endpoint.

    Returns metadata about the file for Collabora Online.
    """
    from application.use_cases.files.get_file import execute as get_file_execute
    file_data = get_file_execute(supabase=ctx.services.supabase, file_id=file_id)
    if not file_data:
        raise HTTPException(status_code=404, detail="File not found")

    can_write = _can_write(request)
    return {
        "BaseFileName": file_data["name"],
        "Size": len(file_data["content"]),
        "OwnerId": "shopify_user",
        "UserId": "shopify_user",
        "UserFriendlyName": "Shopify User",
        "Version": "1.0",
        "SupportsUpdate": can_write,
        "SupportsLocks": False,
        "UserCanWrite": can_write,
        "UserCanNotWriteRelative": not can_write,
    }


@router.get("/wopi/files/{file_id}/contents", summary="WOPI GetFile")
async def wopi_get_file(file_id: str, ctx: AppContext = Depends(get_ctx)) -> Response:
    """WOPI GetFile endpoint.

    Returns the file contents for Collabora Online to display.
    """
    from application.use_cases.files.get_file import execute as get_file_execute
    file_data = get_file_execute(supabase=ctx.services.supabase, file_id=file_id)
    if not file_data:
        raise HTTPException(status_code=404, detail="File not found")

    return Response(
        content=file_data["content"],
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{file_data["name"]}"'},
    )


@router.post("/wopi/files/{file_id}/contents", summary="WOPI PutFile")
async def wopi_put_file(file_id: str, request: Request, ctx: AppContext = Depends(get_ctx)) -> Response:
    """WOPI PutFile endpoint.

    Persists file content updates from Collabora for editable sessions.
    """
    from application.use_cases.files.get_file import execute as get_file_execute
    file_data = get_file_execute(supabase=ctx.services.supabase, file_id=file_id)
    if not file_data:
        raise HTTPException(status_code=404, detail="File not found")
    if not _can_write(request):
        raise HTTPException(status_code=403, detail="Write access denied")

    override = (request.headers.get("X-WOPI-Override") or "").upper()
    if override and override != "PUT":
        raise HTTPException(status_code=400, detail="Unsupported WOPI override")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty file content")

    from application.use_cases.files.save_file import execute as save_file_execute
    save_file_execute(supabase=ctx.services.supabase, file_id=file_id, name=str(file_data.get("name") or f"{file_id}.xlsx"), content=body, content_type=str(file_data.get("content_type") or "application/octet-stream"))
    return Response(status_code=200)


@router.put("/wopi/files/{file_id}/contents", summary="WOPI PutFile (PUT)")
async def wopi_put_file_raw(file_id: str, request: Request, ctx: AppContext = Depends(get_ctx)) -> Response:
    """Fallback for clients sending direct PUT instead of POST+X-WOPI-Override."""
    return await wopi_put_file(file_id=file_id, request=request, ctx=ctx)
