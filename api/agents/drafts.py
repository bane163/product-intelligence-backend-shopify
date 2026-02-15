"""Product draft CRUD routes."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException

from ai.excel_writer import create_excel_bytes
from ai.models import ProductsList

from app_context import AppContext, get_ctx
from .utils import parse_products_json

router = APIRouter()


@router.post("/product-drafts", summary="Save extracted products as draft")
async def save_product_draft(
    products_json: str = Form(...),
    run_id: str | None = Form(None),
    import_mode: str = Form("dry_run"),
    draft_name: str | None = Form(None),
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
        products=products,
    )
    return {
        "draft_id": saved["draft_id"],
        "run_id": saved.get("run_id"),
        "import_mode": saved["import_mode"],
        "draft_name": saved.get("draft_name"),
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
    return {"file_id": file_id, "filename": filename}
