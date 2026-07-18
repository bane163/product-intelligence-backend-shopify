"""Import workspace aggregate routes."""

from fastapi import APIRouter, Depends, Request

from app_context import AppContext, get_ctx
from .utils import require_shop_domain

router = APIRouter()


@router.get("/imports/summary", summary="Get import workspace totals")
async def get_imports_summary(
    request: Request,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, int]:
    """Return lightweight, tenant-scoped counts for the imports workspace."""
    tenant = require_shop_domain(request)
    files = ctx.supabase.list_files(limit=5000, offset=0, shop_domain=tenant)
    drafts = ctx.supabase.list_product_drafts(
        limit=5000, offset=0, shop_domain=tenant
    )
    submitted = ctx.supabase.list_submitted_documents(
        limit=5000, offset=0, shop_domain=tenant
    )
    return {
        "uploaded_files": len(files),
        "drafts": len(drafts),
        "submitted": len(submitted),
    }
