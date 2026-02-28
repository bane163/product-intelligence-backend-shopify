"""Submitted document routes."""

from fastapi import APIRouter, Depends, HTTPException, Request

from app_context import AppContext, get_ctx
from .schemas import BulkDeletePayload, BulkDeleteResult
from .utils import require_shop_domain, resolve_shop_domain

router = APIRouter()


@router.get("/submitted-documents", summary="List submitted documents")
async def list_submitted_documents(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    sort_by: str = "date",
    sort_dir: str = "desc",
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, list[dict]]:
    from application.use_cases.submitted.list_submitted_documents import execute as list_submitted_execute
    tenant = require_shop_domain(request, shop_domain)
    documents = list_submitted_execute(supabase=ctx.supabase, limit=limit, offset=offset, search=search, sort_by=sort_by, sort_dir=sort_dir, shop_domain=tenant)
    return {"submitted_documents": documents}


@router.get("/submitted-documents/{submitted_id}", summary="Get submitted document")
async def get_submitted_document(
    submitted_id: str,
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, dict]:
    from application.use_cases.submitted.get_submitted_document import execute as get_submitted_execute
    tenant = resolve_shop_domain(request, shop_domain)
    document = get_submitted_execute(
        supabase=ctx.supabase,
        submitted_id=submitted_id,
        shop_domain=tenant,
    )
    if not document:
        raise HTTPException(status_code=404, detail="Submitted document not found")
    return {"submitted_document": document}


@router.post("/submitted-documents/{submitted_id}/resume-file", summary="Create preview file from submitted document")
async def create_submitted_document_resume_file(
    submitted_id: str,
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, str]:
    from application.use_cases.submitted.create_submitted_resume_file import execute as create_resume_execute
    try:
        tenant = resolve_shop_domain(request, shop_domain)
        return create_resume_execute(
            supabase=ctx.supabase,
            submitted_id=submitted_id,
            shop_domain=tenant,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Submitted document not found")
    except ValueError:
        raise HTTPException(status_code=400, detail="Submitted document has no products")


@router.delete("/submitted-documents/{submitted_id}", summary="Delete submitted document")
async def delete_submitted_document(
    submitted_id: str,
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, str]:
    from application.use_cases.submitted.delete_submitted_document import execute as delete_submitted_execute
    tenant = resolve_shop_domain(request, shop_domain)
    if not delete_submitted_execute(
        supabase=ctx.supabase,
        submitted_id=submitted_id,
        shop_domain=tenant,
    ):
        raise HTTPException(status_code=404, detail="Submitted document not found")
    return {"status": "deleted", "submitted_id": submitted_id}


@router.post("/submitted-documents/bulk-delete", summary="Bulk delete submitted documents")
async def bulk_delete_submitted_documents(
    payload: BulkDeletePayload,
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> BulkDeleteResult:
    from application.use_cases.submitted.bulk_delete_submitted_documents import execute as bulk_delete_execute
    tenant = resolve_shop_domain(request, shop_domain)
    result = bulk_delete_execute(
        supabase=ctx.supabase,
        ids=payload.ids,
        shop_domain=tenant,
    )
    return BulkDeleteResult(deleted_ids=result["deleted_ids"], failed_ids=result["failed_ids"])
