"""Product intelligence routes."""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app_context import AppContext, get_ctx

router = APIRouter()


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _with_reversibility_flags(suggestion: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(suggestion)
    previous_payload = enriched.get("previous_payload")
    if isinstance(previous_payload, dict):
        is_reversible = previous_payload.get("__is_reversible")
        reason = previous_payload.get("__non_reversible_reason")
        if isinstance(is_reversible, bool):
            enriched["is_reversible"] = is_reversible
        if isinstance(reason, str) and reason.strip():
            enriched["non_reversible_reason"] = reason
    if "is_reversible" not in enriched:
        enriched["is_reversible"] = True
    return enriched


def _resolve_shopify_client(
    *,
    ctx: AppContext,
    shop_domain: str | None,
    shop_access_token: str | None,
):
    if shop_domain and shop_access_token:
        from infrastructure.adapters.shopify_adapter import ShopifyAdapter

        return ShopifyAdapter(shop=shop_domain, token=shop_access_token)
    return ctx.services.shopify


@router.post("/intelligence/audit", summary="Run product data intelligence audit")
async def run_product_intelligence_audit(
    request: Request,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.intelligence_run_audit import execute as run_audit_execute

    payload: dict[str, Any] = {}
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            raw_json = await request.json()
            if isinstance(raw_json, dict):
                payload.update(raw_json)
        except Exception:
            payload = {}
    elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        for key, value in form.items():
            payload[key] = value
    products_json = payload.get("products_json")
    if isinstance(products_json, str) and products_json.strip():
        try:
            payload["products"] = json.loads(products_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid products_json: {exc}")

    submitted_id = payload.get("submitted_id")
    run_id = payload.get("run_id")
    all_products = _to_bool(payload.get("all_products"))
    query = payload.get("query")
    shop_domain = payload.get("shop_domain")
    shop_access_token = payload.get("shop_access_token")
    query_text = str(query).strip() if isinstance(query, str) and query.strip() else None
    raw_limit = payload.get("limit")
    try:
        requested_limit = int(raw_limit) if raw_limit is not None else 250
    except (TypeError, ValueError):
        requested_limit = 250
    requested_limit = max(1, min(requested_limit, 1000))
    products: list[dict[str, Any]] = []

    raw_products = payload.get("products")
    if isinstance(raw_products, list):
        products = [item for item in raw_products if isinstance(item, dict)]

    if submitted_id:
        document = ctx.services.supabase.get_submitted_document(str(submitted_id))
        if not document:
            raise HTTPException(status_code=404, detail="Submitted document not found")
        doc_products = document.get("products")
        if isinstance(doc_products, list):
            products = [item for item in doc_products if isinstance(item, dict)]

    if all_products:
        shopify_client = _resolve_shopify_client(
            ctx=ctx,
            shop_domain=str(shop_domain) if isinstance(shop_domain, str) else None,
            shop_access_token=(
                str(shop_access_token) if isinstance(shop_access_token, str) else None
            ),
        )
        products = await shopify_client.list_products_for_audit(
            query=query_text, limit=requested_limit
        )

    if not products:
        raise HTTPException(status_code=400, detail="No products found for audit")

    try:
        return run_audit_execute(
            supabase=ctx.services.supabase,
            products=products,
            submitted_id=str(submitted_id) if submitted_id else None,
            run_id=str(run_id) if run_id else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/intelligence/shopify-products",
    summary="Search Shopify products for intelligence audit picker",
)
async def search_shopify_products_for_audit(
    query: str | None = None,
    limit: int = 25,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, list[dict[str, Any]]]:
    safe_limit = max(1, min(limit, 100))
    products = await ctx.services.shopify.list_products_for_audit(
        query=query.strip() if query and query.strip() else None,
        limit=safe_limit,
    )
    return {"products": products}


@router.post(
    "/intelligence/shopify-products/search",
    summary="Search Shopify products for intelligence audit picker (POST)",
)
async def search_shopify_products_for_audit_post(
    request: Request,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, list[dict[str, Any]]]:
    data: dict[str, Any] = {}
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            raw_json = await request.json()
            if isinstance(raw_json, dict):
                data.update(raw_json)
        except Exception:
            data = {}
    elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        for key, value in form.items():
            data[key] = value
    query = data.get("query")
    shop_domain = data.get("shop_domain")
    shop_access_token = data.get("shop_access_token")
    raw_limit = data.get("limit")
    try:
        safe_limit = int(raw_limit) if raw_limit is not None else 25
    except (TypeError, ValueError):
        safe_limit = 25
    safe_limit = max(1, min(safe_limit, 100))
    shopify_client = _resolve_shopify_client(
        ctx=ctx,
        shop_domain=str(shop_domain) if isinstance(shop_domain, str) else None,
        shop_access_token=(
            str(shop_access_token) if isinstance(shop_access_token, str) else None
        ),
    )
    products = await shopify_client.list_products_for_audit(
        query=query.strip() if isinstance(query, str) and query.strip() else None,
        limit=safe_limit,
    )
    return {"products": products}


@router.get("/intelligence/audits", summary="List intelligence audits")
async def list_product_intelligence_audits(
    limit: int = 50,
    offset: int = 0,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, list[dict[str, Any]]]:
    from application.use_cases.intelligence_list_audits import execute as list_audits_execute

    audits = list_audits_execute(supabase=ctx.services.supabase, limit=limit, offset=offset)
    return {"audits": audits}


@router.get("/intelligence/audits/{audit_id}", summary="Get intelligence audit")
async def get_product_intelligence_audit(
    audit_id: str,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.intelligence_get_audit import execute as get_audit_execute

    audit = get_audit_execute(supabase=ctx.services.supabase, audit_id=audit_id)
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")
    return {"audit": audit}


@router.get(
    "/intelligence/audits/{audit_id}/suggestions",
    summary="List intelligence suggestions for an audit",
)
async def list_product_intelligence_suggestions(
    audit_id: str,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, list[dict[str, Any]]]:
    from application.use_cases.intelligence_list_suggestions import (
        execute as list_suggestions_execute,
    )

    suggestions = list_suggestions_execute(supabase=ctx.services.supabase, audit_id=audit_id)
    return {
        "suggestions": [
            _with_reversibility_flags(item) if isinstance(item, dict) else item
            for item in suggestions
        ]
    }


@router.post(
    "/intelligence/suggestions/{suggestion_id}/apply",
    summary="Apply one intelligence suggestion",
)
async def apply_product_intelligence_suggestion(
    suggestion_id: str,
    request: Request,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.intelligence_apply_suggestion import (
        execute as apply_suggestion_execute,
    )

    data: dict[str, Any] = {}
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            raw_json = await request.json()
            if isinstance(raw_json, dict):
                data.update(raw_json)
        except Exception:
            data = {}
    elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        for key, value in form.items():
            data[key] = value
    patch_payload: dict[str, Any] | None = None
    raw_patch_payload = data.get("patch_payload")
    raw_patch_payload_json = data.get("patch_payload_json")
    if isinstance(raw_patch_payload, dict):
        patch_payload = raw_patch_payload
    elif isinstance(raw_patch_payload_json, str) and raw_patch_payload_json.strip():
        try:
            parsed_patch_payload = json.loads(raw_patch_payload_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid patch_payload_json: {exc}")
        if not isinstance(parsed_patch_payload, dict):
            raise HTTPException(status_code=400, detail="patch_payload_json must decode to an object")
        patch_payload = parsed_patch_payload
    shop_domain = data.get("shop_domain")
    shop_access_token = data.get("shop_access_token")
    shopify_client = _resolve_shopify_client(
        ctx=ctx,
        shop_domain=str(shop_domain) if isinstance(shop_domain, str) else None,
        shop_access_token=(
            str(shop_access_token) if isinstance(shop_access_token, str) else None
        ),
    )

    try:
        result = await apply_suggestion_execute(
            supabase=ctx.services.supabase,
            shopify=shopify_client,
            suggestion_id=suggestion_id,
            patch_payload=patch_payload,
        )
        suggestion = result.get("suggestion")
        if isinstance(suggestion, dict):
            result["suggestion"] = _with_reversibility_flags(suggestion)
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/intelligence/suggestions/{suggestion_id}/revert",
    summary="Revert one applied intelligence suggestion",
)
async def revert_product_intelligence_suggestion(
    suggestion_id: str,
    request: Request,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.intelligence_revert_suggestion import (
        execute as revert_suggestion_execute,
    )

    data: dict[str, Any] = {}
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            raw_json = await request.json()
            if isinstance(raw_json, dict):
                data.update(raw_json)
        except Exception:
            data = {}
    elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        for key, value in form.items():
            data[key] = value
    shop_domain = data.get("shop_domain")
    shop_access_token = data.get("shop_access_token")
    shopify_client = _resolve_shopify_client(
        ctx=ctx,
        shop_domain=str(shop_domain) if isinstance(shop_domain, str) else None,
        shop_access_token=(
            str(shop_access_token) if isinstance(shop_access_token, str) else None
        ),
    )

    try:
        result = await revert_suggestion_execute(
            supabase=ctx.services.supabase,
            shopify=shopify_client,
            suggestion_id=suggestion_id,
        )
        suggestion = result.get("suggestion")
        if isinstance(suggestion, dict):
            result["suggestion"] = _with_reversibility_flags(suggestion)
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/intelligence/suggestions/apply-bulk",
    summary="Apply multiple intelligence suggestions",
)
async def apply_product_intelligence_suggestions_bulk(
    request: Request,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.intelligence_apply_suggestion import (
        execute as apply_suggestion_execute,
    )

    payload: dict[str, Any] = {}
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            raw_json = await request.json()
            if isinstance(raw_json, dict):
                payload.update(raw_json)
        except Exception:
            payload = {}
    elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        for key, value in form.items():
            payload[key] = value
    raw_ids: Any = payload.get("suggestion_ids")
    raw_ids_json = payload.get("suggestion_ids_json")
    if not isinstance(raw_ids, list) and isinstance(raw_ids_json, str) and raw_ids_json.strip():
        try:
            parsed_ids = json.loads(raw_ids_json)
            if isinstance(parsed_ids, list):
                raw_ids = parsed_ids
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid suggestion_ids_json: {exc}")
    if not isinstance(raw_ids, list):
        raise HTTPException(status_code=400, detail="suggestion_ids must be a list")
    suggestion_ids = [str(item).strip() for item in raw_ids if str(item).strip()]
    if not suggestion_ids:
        raise HTTPException(status_code=400, detail="No suggestion_ids provided")
    shop_domain = payload.get("shop_domain")
    shop_access_token = payload.get("shop_access_token")
    shopify_client = _resolve_shopify_client(
        ctx=ctx,
        shop_domain=str(shop_domain) if isinstance(shop_domain, str) else None,
        shop_access_token=(
            str(shop_access_token) if isinstance(shop_access_token, str) else None
        ),
    )

    results: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for suggestion_id in suggestion_ids:
        try:
            result = await apply_suggestion_execute(
                supabase=ctx.services.supabase,
                shopify=shopify_client,
                suggestion_id=suggestion_id,
            )
            results.append({"suggestion_id": suggestion_id, **result})
        except Exception as exc:
            failed.append({"suggestion_id": suggestion_id, "error": str(exc)})

    return {
        "applied_count": len(results),
        "failed_count": len(failed),
        "results": results,
        "failed": failed,
    }
