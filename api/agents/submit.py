"""Shopify submission routes."""

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException

from app_context import AppContext, get_ctx
from shopify import ShopifyClient
from .utils import parse_products_json

router = APIRouter()


@router.post("/submit-products", summary="Submit extracted products to Shopify")
async def submit_products_to_shopify(
    products_json: str = Form(...),
    import_mode: str = Form("auto"),
    run_id: str | None = Form(None),
    draft_id: str | None = Form(None),
    document_name: str | None = Form(None),
    shop_domain: str | None = Form(None),
    shop_access_token: str | None = Form(None),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.processing.submit_products import execute as submit_execute

    try:
        result = await submit_execute(
            supabase=ctx.services.supabase,
            shopify=ctx.services.shopify,
            tracing=ctx.services.tracing,
            products_json=products_json,
            import_mode=import_mode,
            run_id=run_id,
            draft_id=draft_id,
            document_name=document_name,
            shop_domain=shop_domain,
            shop_access_token=shop_access_token,
        )
        return result
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

