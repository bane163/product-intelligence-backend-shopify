"""Product draft CRUD routes."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException

from ai.excel_writer import create_excel_bytes
from ai.models import ProductsList

from app_context import AppContext, get_ctx
from .schemas import BulkDeletePayload, BulkDeleteResult
from .utils import parse_products_json

router = APIRouter()


@router.post("/product-drafts", summary="Save extracted products as draft")
async def save_product_draft(
    products_json: str = Form(...),
    run_id: str | None = Form(None),
    import_mode: str = Form("auto"),
    draft_name: str | None = Form(None),
    input_file_id: str | None = Form(None),
    input_filename: str | None = Form(None),
    output_file_id: str | None = Form(None),
    output_filename: str | None = Form(None),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    products = parse_products_json(products_json)
    if not products:
        raise HTTPException(status_code=400, detail="No products provided")
    draft_id = str(uuid.uuid4())
    saved = ctx.services.supabase.save_product_draft(
        draft_id=draft_id,
        run_id=run_id,
        import_mode=import_mode,
        draft_name=draft_name,
        input_file_id=input_file_id,
        input_filename=input_filename,
        output_file_id=output_file_id,
        output_filename=output_filename,
        products=products,
    )
    return {
        "draft_id": saved["draft_id"],
        "run_id": saved.get("run_id"),
        "import_mode": saved["import_mode"],
        "draft_name": saved.get("draft_name"),
        "input_file_id": saved.get("input_file_id"),
        "input_filename": saved.get("input_filename"),
        "output_file_id": saved.get("output_file_id"),
        "output_filename": saved.get("output_filename"),
        "product_count": saved["product_count"],
        "first_product_title": saved.get("first_product_title"),
        "created_at": saved.get("created_at"),
    }


@router.get("/product-drafts", summary="List product drafts")
async def list_product_drafts(
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    sort_by: str = "date",
    sort_dir: str = "desc",
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    drafts = ctx.services.supabase.list_product_drafts(
        limit=limit,
        offset=offset,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    return {"drafts": drafts}


@router.get("/product-drafts/{draft_id}", summary="Get product draft")
async def get_product_draft(draft_id: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    draft = ctx.services.supabase.get_product_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"draft": draft}


@router.post("/product-drafts/{draft_id}/resume-file", summary="Create preview file from draft")
async def create_product_draft_resume_file(
    draft_id: str, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    draft = ctx.services.supabase.get_product_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    products = draft.get("products")
    if not isinstance(products, list) or not products:
        raise HTTPException(status_code=400, detail="Draft has no products")

    existing_output_file_id = draft.get("output_file_id")
    existing_output_filename = draft.get("output_filename")
    if isinstance(existing_output_file_id, str) and existing_output_file_id:
        existing_file = ctx.services.supabase.get_file(existing_output_file_id)
        if existing_file:
            resolved_name = (
                existing_output_filename
                if isinstance(existing_output_filename, str) and existing_output_filename
                else existing_file.get("name") or f"draft-{draft_id[:8]}.xlsx"
            )
            return {"file_id": existing_output_file_id, "filename": resolved_name}

    parsed = ProductsList.model_validate({"products": products})
    output_bytes = create_excel_bytes(parsed)
    file_id = str(uuid.uuid4())
    filename = f"draft-{draft_id[:8]}.xlsx"
    ctx.services.supabase.save_file(
        file_id=file_id,
        name=filename,
        content=output_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    ctx.services.supabase.save_product_draft(
        draft_id=draft_id,
        run_id=draft.get("run_id") if isinstance(draft.get("run_id"), str) else None,
        import_mode=(
            draft.get("import_mode")
            if isinstance(draft.get("import_mode"), str) and draft.get("import_mode")
            else "auto"
        ),
        draft_name=draft.get("draft_name") if isinstance(draft.get("draft_name"), str) else None,
        input_file_id=(
            draft.get("input_file_id") if isinstance(draft.get("input_file_id"), str) else None
        ),
        input_filename=(
            draft.get("input_filename") if isinstance(draft.get("input_filename"), str) else None
        ),
        output_file_id=file_id,
        output_filename=filename,
        products=products,
    )
    return {"file_id": file_id, "filename": filename}


@router.delete("/product-drafts/{draft_id}", summary="Delete product draft")
async def delete_product_draft(draft_id: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, str]:
    if not ctx.services.supabase.delete_product_draft(draft_id):
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"status": "deleted", "draft_id": draft_id}


@router.post("/product-drafts/bulk-delete", summary="Bulk delete product drafts")
async def bulk_delete_product_drafts(
    payload: BulkDeletePayload, ctx: AppContext = Depends(get_ctx)
) -> BulkDeleteResult:
    deleted_ids: list[str] = []
    failed_ids: list[str] = []
    for draft_id in payload.ids:
        if ctx.services.supabase.delete_product_draft(draft_id):
            deleted_ids.append(draft_id)
        else:
            failed_ids.append(draft_id)
    return BulkDeleteResult(deleted_ids=deleted_ids, failed_ids=failed_ids)
