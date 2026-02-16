"""Shopify submission routes."""

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException

from app_context import AppContext, get_ctx
from shopify import ShopifyClient
from .utils import parse_products_json
from .run_event_emitter import RunEventEmitter

router = APIRouter()
shopify_client = ShopifyClient()


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
    current_run_id = run_id or str(uuid.uuid4())
    emitter = RunEventEmitter(ctx=ctx, run_id=current_run_id)
    emit_and_persist = emitter.emit_and_persist

    ctx.services.supabase.create_or_update_run(
        current_run_id,
        {
            "status": "running",
            "source": "shopify_submit",
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    emit_and_persist(phase="submit_start", message="Starting Shopify submit")
    submit_client = (
        ShopifyClient(shop=shop_domain, token=shop_access_token)
        if (shop_domain or shop_access_token)
        else shopify_client
    )

    products = parse_products_json(products_json)
    if not products:
        emit_and_persist(
            phase="workflow_error",
            message="No products provided for submit",
            level="error",
            error="No products provided",
        )
        ctx.services.supabase.finalize_run(current_run_id, status="error", error="No products provided")
        ctx.services.tracing.complete_run(current_run_id)
        raise HTTPException(status_code=400, detail="No products provided")
    emit_and_persist(
        phase="submit_products_loaded",
        message="Loaded products for submit",
        payload_preview={
            "count": len(products),
            "mode": "auto",
            "shop_domain": shop_domain,
            "has_token": bool(shop_access_token),
        },
    )

    def _extract_first_sku(item: dict[str, Any]) -> str | None:
        sku = item.get("sku")
        if isinstance(sku, str) and sku.strip():
            return sku.strip()
        variants = item.get("variants")
        if isinstance(variants, list):
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                variant_sku = variant.get("sku")
                if isinstance(variant_sku, str) and variant_sku.strip():
                    return variant_sku.strip()
        return None

    results: list[dict[str, Any]] = []
    success_count = 0
    for index, product in enumerate(products):
        title = product.get("title")
        if not title:
            emit_and_persist(
                phase="submit_item_error",
                message=f"Product at index {index} missing title",
                level="error",
                error="Missing title",
                payload_preview={"index": index},
            )
            results.append(
                {
                    "index": index,
                    "title": None,
                    "status": "failed",
                    "errors": [{"field": ["title"], "message": "Missing title"}],
                }
            )
            continue
        try:
            gid = product.get("id") or product.get("shopify_gid")
            resolve_reason = "id" if gid else None
            if not gid and product.get("handle"):
                gid = await submit_client.find_product_id_by_handle(str(product["handle"]))
                if gid:
                    resolve_reason = "handle"
            if not gid:
                sku = _extract_first_sku(product)
                if sku:
                    gid = await submit_client.find_product_id_by_sku(sku)
                    if gid:
                        resolve_reason = "sku"

            if gid:
                emit_and_persist(
                    phase="submit_item_mode",
                    message=f"Resolved update target for '{title}'",
                    payload_preview={
                        "index": index,
                        "title": title,
                        "mode": "update",
                        "reason": resolve_reason,
                        "shopify_product_id": gid,
                    },
                )
                response = await submit_client.update_product_from_input({**product, "id": gid})
                payload = response.get("data", {}).get("productUpdate", {})
                mode_used = "update"
            else:
                emit_and_persist(
                    phase="submit_item_mode",
                    message=f"No existing match found for '{title}', creating",
                    payload_preview={"index": index, "title": title, "mode": "create"},
                )
                response = await submit_client.create_product_from_input(product)
                payload = response.get("data", {}).get("productCreate", {})
                mode_used = "create"

            errors = payload.get("userErrors") or []
            product_data = payload.get("product") or {}
            if errors:
                emit_and_persist(
                    phase="submit_item_error",
                    message=f"Shopify rejected product '{title}'",
                    level="error",
                    error=str(errors),
                    payload_preview={"index": index, "title": title},
                )
                results.append(
                    {
                        "index": index,
                        "title": title,
                        "status": "failed",
                        "shopify_product_id": product_data.get("id"),
                        "errors": errors,
                    }
                )
            else:
                success_count += 1
                emit_and_persist(
                    phase="submit_item_success",
                    message=f"Submitted product '{product_data.get('title', title)}' via {mode_used}",
                    payload_preview={
                        "index": index,
                        "mode": mode_used,
                        "shopify_product_id": product_data.get("id"),
                    },
                )
                results.append(
                    {
                        "index": index,
                        "title": product_data.get("title", title),
                        "status": "success",
                        "mode": mode_used,
                        "shopify_product_id": product_data.get("id"),
                        "errors": [],
                    }
                )
        except Exception as exc:
            emit_and_persist(
                phase="submit_item_error",
                message=f"Submit failed for product '{title}'",
                level="error",
                error=str(exc),
                payload_preview={"index": index, "title": title},
            )
            results.append(
                {
                    "index": index,
                    "title": title,
                    "status": "failed",
                    "errors": [{"field": None, "message": str(exc)}],
                }
            )

    status = "success" if success_count == len(products) else "error"
    submitted_id: str | None = None
    if status == "success":
        inferred_name = document_name
        if not inferred_name and draft_id:
            draft = ctx.services.supabase.get_product_draft(draft_id)
            if draft:
                inferred_name = draft.get("draft_name") or draft.get("first_product_title")
        if not inferred_name:
            inferred_name = str(products[0].get("title") or "Submitted document")
        submitted = ctx.services.supabase.save_submitted_document(
            submitted_id=str(uuid.uuid4()),
            run_id=current_run_id,
            draft_id=draft_id,
            name=str(inferred_name),
            import_mode="auto",
            product_count=len(products),
            products=products,
        )
        submitted_id = str(submitted.get("submitted_id"))
        emit_and_persist(
            phase="submit_recorded",
            message="Recorded submitted document",
            payload_preview={"submitted_id": submitted_id, "draft_id": draft_id},
        )
    ctx.services.supabase.finalize_run(
        current_run_id,
        status=status,
        error=None if status == "success" else "Some products failed to submit",
        extra_fields={
            "output_filename": "submit:auto",
        },
    )
    emit_and_persist(
        phase="request_done",
        message="Completed Shopify submit",
        payload_preview={"success_count": success_count, "failed_count": len(products) - success_count},
    )
    ctx.services.tracing.complete_run(current_run_id)

    return {
        "run_id": current_run_id,
        "submitted_id": submitted_id,
        "import_mode": "auto",
        "total": len(products),
        "success_count": success_count,
        "failed_count": len(products) - success_count,
        "results": results,
    }
