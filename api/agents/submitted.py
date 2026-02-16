"""Submitted document routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException

from ai.excel_writer import create_excel_bytes
from ai.models import ProductsList

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
    documents = ctx.services.supabase.list_submitted_documents(
        limit=limit,
        offset=offset,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    return {"submitted_documents": documents}


@router.get("/submitted-documents/{submitted_id}", summary="Get submitted document")
async def get_submitted_document(submitted_id: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, dict]:
    document = ctx.services.supabase.get_submitted_document(submitted_id)
    if not document:
        raise HTTPException(status_code=404, detail="Submitted document not found")
    return {"submitted_document": document}


@router.post("/submitted-documents/{submitted_id}/resume-file", summary="Create preview file from submitted document")
async def create_submitted_document_resume_file(
    submitted_id: str, ctx: AppContext = Depends(get_ctx)
) -> dict[str, str]:
    document = ctx.services.supabase.get_submitted_document(submitted_id)
    if not document:
        raise HTTPException(status_code=404, detail="Submitted document not found")
    products = document.get("products")
    if not isinstance(products, list) or not products:
        raise HTTPException(status_code=400, detail="Submitted document has no products")

    parsed = ProductsList.model_validate({"products": products})
    output_bytes = create_excel_bytes(parsed)
    file_id = str(uuid.uuid4())
    filename = f"submitted-{submitted_id[:8]}.xlsx"
    ctx.services.supabase.save_file(
        file_id=file_id,
        name=filename,
        content=output_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    return {"file_id": file_id, "filename": filename}


@router.delete("/submitted-documents/{submitted_id}", summary="Delete submitted document")
async def delete_submitted_document(
    submitted_id: str, ctx: AppContext = Depends(get_ctx)
) -> dict[str, str]:
    if not ctx.services.supabase.delete_submitted_document(submitted_id):
        raise HTTPException(status_code=404, detail="Submitted document not found")
    return {"status": "deleted", "submitted_id": submitted_id}


@router.post("/submitted-documents/bulk-delete", summary="Bulk delete submitted documents")
async def bulk_delete_submitted_documents(
    payload: BulkDeletePayload, ctx: AppContext = Depends(get_ctx)
) -> BulkDeleteResult:
    deleted_ids: list[str] = []
    failed_ids: list[str] = []
    for submitted_id in payload.ids:
        if ctx.services.supabase.delete_submitted_document(submitted_id):
            deleted_ids.append(submitted_id)
        else:
            failed_ids.append(submitted_id)
    return BulkDeleteResult(deleted_ids=deleted_ids, failed_ids=failed_ids)
