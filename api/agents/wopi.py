"""WOPI endpoints for Collabora Online integration."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from typing import Any

from app_context import AppContext, get_ctx
from .wopi_tokens import validate_wopi_token

router = APIRouter()


def _can_write(request: Request) -> bool:
    return getattr(request.state, "wopi_permission", None) == "edit"


@router.get("/wopi/files/{file_id}", summary="WOPI CheckFileInfo")
async def wopi_check_file_info(
    file_id: str, request: Request, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    """WOPI CheckFileInfo endpoint.

    Returns metadata about the file for Collabora Online.
    """
    token = validate_wopi_token(request, file_id)
    request.state.wopi_permission = token["permission"]
    from application.use_cases.files.get_file import execute as get_file_execute
    file_data = get_file_execute(supabase=ctx.supabase, file_id=file_id, shop_domain=token["shop"])
    if not file_data:
        raise HTTPException(status_code=404, detail="File not found")

    can_write = _can_write(request)
    payload = {
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
    post_message_origin = token.get("post_message_origin")
    if isinstance(post_message_origin, str) and post_message_origin:
        payload["PostMessageOrigin"] = post_message_origin
    from services.source_link_trace import record as record_source_link_trace
    record_source_link_trace(
        component="wopi",
        stage="check_file_info",
        status="ok",
        attempt_id=token.get("trace_attempt_id"),
        source_file_id=file_id,
        details={"origin": post_message_origin or "missing"},
    )
    return payload


@router.get("/wopi/files/{file_id}/contents", summary="WOPI GetFile")
async def wopi_get_file(file_id: str, request: Request, ctx: AppContext = Depends(get_ctx)) -> Response:
    """WOPI GetFile endpoint.

    Returns the file contents for Collabora Online to display.
    """
    token = validate_wopi_token(request, file_id)
    from application.use_cases.files.get_file import execute as get_file_execute
    file_data = get_file_execute(supabase=ctx.supabase, file_id=file_id, shop_domain=token["shop"])
    if not file_data:
        raise HTTPException(status_code=404, detail="File not found")

    from services.source_link_trace import record as record_source_link_trace
    record_source_link_trace(
        component="wopi",
        stage="get_file",
        status="ok",
        attempt_id=token.get("trace_attempt_id"),
        source_file_id=file_id,
    )

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
    token = validate_wopi_token(request, file_id)
    request.state.wopi_permission = token["permission"]
    file_data = get_file_execute(supabase=ctx.supabase, file_id=file_id, shop_domain=token["shop"])
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
    save_file_execute(
        supabase=ctx.supabase,
        file_id=file_id,
        name=str(file_data.get("name") or f"{file_id}.xlsx"),
        content=body,
        content_type=str(file_data.get("content_type") or "application/octet-stream"),
        file_origin=(
            str(file_data.get("file_origin"))
            if isinstance(file_data.get("file_origin"), str)
            else None
        ),
    )
    return Response(status_code=200)


@router.put("/wopi/files/{file_id}/contents", summary="WOPI PutFile (PUT)")
async def wopi_put_file_raw(file_id: str, request: Request, ctx: AppContext = Depends(get_ctx)) -> Response:
    """Fallback for clients sending direct PUT instead of POST+X-WOPI-Override."""
    return await wopi_put_file(file_id=file_id, request=request, ctx=ctx)
