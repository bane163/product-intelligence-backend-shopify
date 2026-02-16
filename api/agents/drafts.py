"""Product draft CRUD routes."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException

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
    from application.use_cases.save_product_draft import execute as save_product_draft_execute

    saved = save_product_draft_execute(supabase=ctx.services.supabase, draft_id=draft_id, run_id=run_id, import_mode=import_mode, draft_name=draft_name, input_file_id=input_file_id, input_filename=input_filename, output_file_id=output_file_id, output_filename=output_filename, products=products)
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
    from application.use_cases.list_product_drafts import execute as list_drafts_execute
    drafts = list_drafts_execute(supabase=ctx.services.supabase, limit=limit, offset=offset, search=search, sort_by=sort_by, sort_dir=sort_dir)
    return {"drafts": drafts}


@router.get("/product-drafts/{draft_id}", summary="Get product draft")
async def get_product_draft(draft_id: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    from application.use_cases.get_product_draft import execute as get_draft_execute
    draft = get_draft_execute(supabase=ctx.services.supabase, draft_id=draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"draft": draft}


@router.post("/product-drafts/{draft_id}/resume-file", summary="Create preview file from draft")
async def create_product_draft_resume_file(
    draft_id: str, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    from application.use_cases.create_draft_resume_file import execute as create_resume_execute
    try:
        return create_resume_execute(supabase=ctx.services.supabase, draft_id=draft_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Draft not found")
    except ValueError:
        raise HTTPException(status_code=400, detail="Draft has no products")


@router.delete("/product-drafts/{draft_id}", summary="Delete product draft")
async def delete_product_draft(draft_id: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, str]:
    from application.use_cases.delete_product_draft import execute as delete_draft_execute
    if not delete_draft_execute(supabase=ctx.services.supabase, draft_id=draft_id):
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"status": "deleted", "draft_id": draft_id}


@router.post("/product-drafts/bulk-delete", summary="Bulk delete product drafts")
async def bulk_delete_product_drafts(
    payload: BulkDeletePayload, ctx: AppContext = Depends(get_ctx)
) -> BulkDeleteResult:
    from application.use_cases.bulk_delete_product_drafts import execute as bulk_delete_execute
    result = bulk_delete_execute(supabase=ctx.services.supabase, ids=payload.ids)
    return BulkDeleteResult(deleted_ids=result["deleted_ids"], failed_ids=result["failed_ids"])
