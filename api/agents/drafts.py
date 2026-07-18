"""Product draft CRUD routes."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from app_context import AppContext, get_ctx
from .schemas import BulkDeletePayload, BulkDeleteResult
from .utils import parse_products_json, require_shop_domain, resolve_shop_domain

router = APIRouter()


def _enrich_token_usage(drafts: list[dict[str, Any]], ctx: AppContext, tenant: str) -> None:
    related_by_draft: list[list[str]] = []
    all_ids: list[str] = []
    for draft in drafts:
        ids = list(dict.fromkeys(str(draft.get(key) or "").strip() for key in
                                 ("run_id", "extraction_run_id", "submit_run_id")))
        ids = [run_id for run_id in ids if run_id]
        related_by_draft.append(ids)
        all_ids.extend(ids)
    summaries = ctx.supabase.runs.get_run_summaries(list(dict.fromkeys(all_ids)), shop_domain=tenant)
    for draft, ids in zip(drafts, related_by_draft):
        recorded = [summaries[run_id] for run_id in ids if run_id in summaries and
                    summaries[run_id].get("total_tokens") is not None]
        draft["token_usage"] = None if not recorded else {
            "prompt_tokens": sum(int(run.get("prompt_tokens") or 0) for run in recorded),
            "completion_tokens": sum(int(run.get("completion_tokens") or 0) for run in recorded),
            "total_tokens": sum(int(run.get("total_tokens") or 0) for run in recorded),
            "recorded_run_count": len(recorded),
            "related_run_count": len(ids),
        }


@router.post("/product-drafts", summary="Save extracted products as draft")
async def save_product_draft(
    request: Request,
    products_json: str = Form(...),
    run_id: str | None = Form(None),
    import_mode: str = Form("auto"),
    draft_name: str | None = Form(None),
    shop_domain: str | None = Form(None),
    input_file_id: str | None = Form(None),
    input_filename: str | None = Form(None),
    output_file_id: str | None = Form(None),
    output_filename: str | None = Form(None),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    products = parse_products_json(products_json)
    if not products:
        raise HTTPException(status_code=400, detail="No products provided")
    tenant = resolve_shop_domain(request, shop_domain)
    draft_id = str(uuid.uuid4())
    from application.use_cases.drafts.save_product_draft import execute as save_product_draft_execute

    saved = save_product_draft_execute(supabase=ctx.supabase, draft_id=draft_id, run_id=run_id, import_mode=import_mode, draft_name=draft_name, shop_domain=tenant, input_file_id=input_file_id, input_filename=input_filename, output_file_id=output_file_id, output_filename=output_filename, products=products)
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
    request: Request,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    sort_by: str = "date",
    sort_dir: str = "desc",
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.drafts.list_product_drafts import execute as list_drafts_execute
    tenant = require_shop_domain(request, shop_domain)
    drafts = list_drafts_execute(supabase=ctx.supabase, limit=limit, offset=offset, search=search, sort_by=sort_by, sort_dir=sort_dir, shop_domain=tenant)
    _enrich_token_usage(drafts, ctx, tenant)
    return {"drafts": drafts}


@router.get("/product-drafts/{draft_id}", summary="Get product draft")
async def get_product_draft(
    draft_id: str,
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.drafts.get_product_draft import execute as get_draft_execute
    tenant = resolve_shop_domain(request, shop_domain)
    draft = get_draft_execute(supabase=ctx.supabase, draft_id=draft_id, shop_domain=tenant)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"draft": draft}


@router.post("/product-drafts/{draft_id}/resume-file", summary="Create preview file from draft")
async def create_product_draft_resume_file(
    draft_id: str,
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.drafts.create_draft_resume_file import execute as create_resume_execute
    try:
        tenant = resolve_shop_domain(request, shop_domain)
        return create_resume_execute(
            supabase=ctx.supabase,
            draft_id=draft_id,
            shop_domain=tenant,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Draft not found")
    except ValueError:
        raise HTTPException(status_code=400, detail="Draft has no products")


@router.delete("/product-drafts/{draft_id}", summary="Delete product draft")
async def delete_product_draft(
    draft_id: str,
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, str]:
    from application.use_cases.drafts.delete_product_draft import execute as delete_draft_execute
    tenant = resolve_shop_domain(request, shop_domain)
    if not delete_draft_execute(
        supabase=ctx.supabase,
        draft_id=draft_id,
        shop_domain=tenant,
    ):
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"status": "deleted", "draft_id": draft_id}


@router.post("/product-drafts/bulk-delete", summary="Bulk delete product drafts")
async def bulk_delete_product_drafts(
    payload: BulkDeletePayload,
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> BulkDeleteResult:
    from application.use_cases.drafts.bulk_delete_product_drafts import execute as bulk_delete_execute
    tenant = resolve_shop_domain(request, shop_domain)
    result = bulk_delete_execute(
        supabase=ctx.supabase,
        ids=payload.ids,
        shop_domain=tenant,
    )
    return BulkDeleteResult(deleted_ids=result["deleted_ids"], failed_ids=result["failed_ids"])
