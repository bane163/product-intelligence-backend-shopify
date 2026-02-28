"""Shopify submission routes."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import JSONResponse

from app_context import AppContext, get_ctx
from shopify import (
    ShopifyClient,
)  # kept for route-level monkeypatch compatibility in tests
from .utils import parse_products_json

router = APIRouter()
LOG = logging.getLogger(__name__)


def _optional_str(entry: dict[str, Any] | None, key: str) -> str | None:
    if not isinstance(entry, dict):
        return None
    value = entry.get(key)
    return value if isinstance(value, str) else None


def _draft_products(entry: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(entry, dict):
        return []
    raw_products = entry.get("products")
    if not isinstance(raw_products, list):
        return []
    return [item for item in raw_products if isinstance(item, dict)]


def _save_submit_draft_state(
    *,
    ctx: AppContext,
    draft_id: str,
    shop_domain: str | None,
    fallback_run_id: str | None,
    fallback_name: str | None,
    submit_status: str | None,
    submit_run_id: str | None,
    submit_error: str | None,
) -> None:
    from application.use_cases.drafts.get_product_draft import (
        execute as get_draft_execute,
    )
    from application.use_cases.drafts.save_product_draft import (
        execute as save_draft_execute,
    )

    existing = get_draft_execute(
        supabase=ctx.supabase,
        draft_id=draft_id,
        shop_domain=shop_domain,
    )
    if not isinstance(existing, dict):
        return
    save_draft_execute(
        supabase=ctx.supabase,
        draft_id=draft_id,
        run_id=_optional_str(existing, "run_id") or fallback_run_id,
        import_mode=_optional_str(existing, "import_mode") or "auto",
        draft_name=_optional_str(existing, "draft_name") or fallback_name,
        shop_domain=_optional_str(existing, "shop_domain") or shop_domain,
        input_file_id=_optional_str(existing, "input_file_id"),
        input_filename=_optional_str(existing, "input_filename"),
        output_file_id=_optional_str(existing, "output_file_id"),
        output_filename=_optional_str(existing, "output_filename"),
        extraction_status=_optional_str(existing, "extraction_status"),
        extraction_run_id=_optional_str(existing, "extraction_run_id"),
        extraction_error=_optional_str(existing, "extraction_error"),
        submit_status=submit_status,
        submit_run_id=submit_run_id,
        submit_error=submit_error,
        require_lifecycle_columns=True,
        products=_draft_products(existing),
    )


@router.post("/submit-products", summary="Submit extracted products to Shopify")
async def submit_products_to_shopify(
    products_json: str | None = Form(None),
    import_mode: str = Form("auto"),
    run_id: str | None = Form(None),
    draft_id: str | None = Form(None),
    submitted_id: str | None = Form(None),
    document_name: str | None = Form(None),
    shop_domain: str | None = Form(None),
    shop_access_token: str | None = Form(None),
    offload: bool = Form(False),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.processing.submit_products import (
        execute as submit_execute,
    )
    from application.use_cases.drafts.save_product_draft import (
        execute as save_draft_execute,
    )
    from application.use_cases.submitted.get_submitted_document import (
        execute as get_submitted_execute,
    )

    resolved_draft_id = draft_id
    if offload:
        effective_run_id = run_id or str(uuid.uuid4())
        if not resolved_draft_id and submitted_id:
            submitted_document = get_submitted_execute(
                supabase=ctx.supabase,
                submitted_id=submitted_id,
                shop_domain=shop_domain,
            )
            if isinstance(submitted_document, dict):
                inferred_draft_id = submitted_document.get("draft_id")
                if isinstance(inferred_draft_id, str) and inferred_draft_id.strip():
                    resolved_draft_id = inferred_draft_id.strip()

        if not resolved_draft_id and products_json:
            products = parse_products_json(products_json)
            if products:
                resolved_draft_id = str(uuid.uuid4())
                save_draft_execute(
                    supabase=ctx.supabase,
                    draft_id=resolved_draft_id,
                    run_id=effective_run_id,
                    import_mode=import_mode,
                    draft_name=document_name,
                    shop_domain=shop_domain,
                    products=products,
                    extraction_status="succeeded",
                    extraction_run_id=None,
                    extraction_error=None,
                    submit_status="queued",
                    submit_run_id=effective_run_id,
                    submit_error=None,
                )

        if not resolved_draft_id:
            raise HTTPException(
                status_code=400,
                detail="Offloaded submit requires draft_id or products_json",
            )

        try:
            _save_submit_draft_state(
                ctx=ctx,
                draft_id=resolved_draft_id,
                shop_domain=shop_domain,
                fallback_run_id=effective_run_id,
                fallback_name=document_name,
                submit_status="queued",
                submit_run_id=effective_run_id,
                submit_error=None,
            )
            ctx.supabase.runs.create_or_update_run(
                effective_run_id,
                {
                    "status": "queued",
                    "source": "shopify_submit",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "attempt": 1,
                    "shop_domain": shop_domain,
                },
            )
            ctx.supabase.runs.enqueue_offload_job(
                str(uuid.uuid4()),
                {
                    "queue_name": "offload",
                    "job_type": "shopify_submit",
                    "status": "queued",
                    "run_id": effective_run_id,
                    "draft_id": resolved_draft_id,
                    "submitted_id": submitted_id,
                    "shop_domain": shop_domain,
                    "payload": {
                        "import_mode": import_mode,
                        "document_name": document_name,
                        "products_json": products_json,
                        "shop_access_token": shop_access_token,
                        "has_shop_access_token": bool(shop_access_token),
                    },
                },
                require_persistent_queue=True,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return JSONResponse(
            status_code=202,
            content={
                "run_id": effective_run_id,
                "draft_id": resolved_draft_id,
                "submitted_id": submitted_id,
                "status": "queued",
            },
        )

    try:
        result = await submit_execute(
            supabase=ctx.supabase,
            shopify=ctx.services.shopify,
            tracing=ctx.services.tracing,
            products_json=products_json,
            import_mode=import_mode,
            run_id=run_id,
            draft_id=resolved_draft_id,
            submitted_id=submitted_id,
            document_name=document_name,
            shop_domain=shop_domain,
            shop_access_token=shop_access_token,
        )
        return result
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
