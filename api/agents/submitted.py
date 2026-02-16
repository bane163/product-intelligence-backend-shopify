"""Submitted document routes."""

from fastapi import APIRouter, Depends, HTTPException

from app_context import AppContext, get_ctx
from .schemas import BulkDeletePayload, BulkDeleteResult

router = APIRouter()


@router.get("/submitted-documents", summary="List submitted documents")
async def list_submitted_documents(
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    sort_by: str = "date",
    sort_dir: str = "desc",
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, list[dict]]:
    from application.use_cases.submitted.list_submitted_documents import execute as list_submitted_execute
    documents = list_submitted_execute(supabase=ctx.services.supabase, limit=limit, offset=offset, search=search, sort_by=sort_by, sort_dir=sort_dir)
    return {"submitted_documents": documents}


@router.get("/submitted-documents/{submitted_id}", summary="Get submitted document")
async def get_submitted_document(submitted_id: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, dict]:
    from application.use_cases.submitted.get_submitted_document import execute as get_submitted_execute
    document = get_submitted_execute(supabase=ctx.services.supabase, submitted_id=submitted_id)
    if not document:
        raise HTTPException(status_code=404, detail="Submitted document not found")
    return {"submitted_document": document}


@router.post("/submitted-documents/{submitted_id}/resume-file", summary="Create preview file from submitted document")
async def create_submitted_document_resume_file(
    submitted_id: str, ctx: AppContext = Depends(get_ctx)
) -> dict[str, str]:
    from application.use_cases.submitted.create_submitted_resume_file import execute as create_resume_execute
    try:
        return create_resume_execute(supabase=ctx.services.supabase, submitted_id=submitted_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Submitted document not found")
    except ValueError:
        raise HTTPException(status_code=400, detail="Submitted document has no products")


@router.delete("/submitted-documents/{submitted_id}", summary="Delete submitted document")
async def delete_submitted_document(
    submitted_id: str, ctx: AppContext = Depends(get_ctx)
) -> dict[str, str]:
    from application.use_cases.submitted.delete_submitted_document import execute as delete_submitted_execute
    if not delete_submitted_execute(supabase=ctx.services.supabase, submitted_id=submitted_id):
        raise HTTPException(status_code=404, detail="Submitted document not found")
    return {"status": "deleted", "submitted_id": submitted_id}


@router.post("/submitted-documents/bulk-delete", summary="Bulk delete submitted documents")
async def bulk_delete_submitted_documents(
    payload: BulkDeletePayload, ctx: AppContext = Depends(get_ctx)
) -> BulkDeleteResult:
    from application.use_cases.submitted.bulk_delete_submitted_documents import execute as bulk_delete_execute
    result = bulk_delete_execute(supabase=ctx.services.supabase, ids=payload.ids)
    return BulkDeleteResult(deleted_ids=result["deleted_ids"], failed_ids=result["failed_ids"])
