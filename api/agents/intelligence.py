"""Product intelligence routes."""

import json
import uuid
from datetime import datetime, timezone
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


def _normalize_shop_domain(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _resolve_shop_domain(request: Request, candidate: Any = None) -> str | None:
    header_shop = _normalize_shop_domain(request.headers.get("x-shop-domain"))
    body_shop = _normalize_shop_domain(candidate)
    if header_shop and body_shop and header_shop != body_shop:
        raise HTTPException(status_code=403, detail="shop_domain mismatch")
    return header_shop or body_shop


def _require_shop_domain(request: Request, candidate: Any = None) -> str:
    shop_domain = _resolve_shop_domain(request, candidate)
    if not shop_domain:
        raise HTTPException(status_code=400, detail="Missing shop_domain")
    return shop_domain


def _resolve_shop_access_token(request: Request, candidate: Any = None) -> str | None:
    header_token = request.headers.get("x-shop-access-token")
    if isinstance(header_token, str) and header_token.strip():
        return header_token.strip()
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return None


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


NORMALIZATION_CATEGORY_KEYS = (
    "size_alias",
    "mixed_units",
    "structured_unstructured_size",
    "dimensions_format",
    "supplier_size",
    "variant_ordering",
    "description_extraction",
    "children_size",
    "missing_options",
)


def _default_unit_system_from_request(request: Request) -> str:
    accept_language = str(request.headers.get("accept-language") or "").strip()
    if "-us" in accept_language.lower():
        return "imperial"
    return "metric"


def _default_normalization_settings(request: Request) -> dict[str, Any]:
    return {
        "unit_system": _default_unit_system_from_request(request),
        "locale_default_unit_system": _default_unit_system_from_request(request),
        "confidence_threshold": None,
        "categories": {key: True for key in NORMALIZATION_CATEGORY_KEYS},
    }


def _coerce_normalization_settings(
    payload: dict[str, Any],
    *,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    raw_unit_system = str(payload.get("unit_system") or fallback.get("unit_system") or "metric").strip().lower()
    unit_system = raw_unit_system if raw_unit_system in {"metric", "imperial"} else str(fallback.get("unit_system") or "metric")

    raw_locale_default = payload.get("locale_default_unit_system", fallback.get("locale_default_unit_system"))
    locale_default_unit_system = str(raw_locale_default).strip().lower() if isinstance(raw_locale_default, str) else None
    if locale_default_unit_system not in {"metric", "imperial"}:
        locale_default_unit_system = str(fallback.get("locale_default_unit_system") or "").strip().lower() or None

    raw_confidence = payload.get("confidence_threshold", fallback.get("confidence_threshold"))
    if raw_confidence in (None, ""):
        confidence_threshold = None
    elif isinstance(raw_confidence, (int, float)):
        confidence_threshold = max(0.0, min(1.0, float(raw_confidence)))
    else:
        raise HTTPException(status_code=400, detail="Invalid confidence_threshold")

    raw_categories = payload.get("categories") if isinstance(payload.get("categories"), dict) else {}
    fallback_categories = fallback.get("categories") if isinstance(fallback.get("categories"), dict) else {}
    categories = {
        key: (
            raw_categories[key]
            if key in raw_categories and isinstance(raw_categories[key], bool)
            else bool(fallback_categories.get(key, True))
        )
        for key in NORMALIZATION_CATEGORY_KEYS
    }

    return {
        "unit_system": unit_system,
        "locale_default_unit_system": locale_default_unit_system,
        "confidence_threshold": confidence_threshold,
        "categories": categories,
    }


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
    shop_domain = _require_shop_domain(request, payload.get("shop_domain"))
    shop_access_token = _resolve_shop_access_token(request, payload.get("shop_access_token"))
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

    run_id_value = str(run_id).strip() if isinstance(run_id, str) and run_id.strip() else str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    ctx.services.supabase.create_or_update_run(
        run_id_value,
        {
            "status": "running",
            "source": "product_intelligence_audit",
            "started_at": started_at.isoformat(),
        },
    )
    from application.services.run_event_emitter import RunEventEmitter

    emitter = RunEventEmitter(
        tracing=ctx.services.tracing,
        supabase=ctx.services.supabase,
        run_id=run_id_value,
    )
    emitter.emit_and_persist(
        phase="audit_request_received",
        message="Received product intelligence audit request",
        payload_preview={
            "products_count": len(products),
            "all_products": all_products,
            "submitted_id": str(submitted_id) if submitted_id else None,
        },
    )

    try:
        result = await run_audit_execute(
            supabase=ctx.services.supabase,
            products=products,
            submitted_id=str(submitted_id) if submitted_id else None,
            run_id=run_id_value,
            shop_domain=shop_domain,
            trace_event=emitter.trace_event,
        )
        emitter.emit_and_persist(
            phase="audit_completed",
            message="Product intelligence audit completed",
            payload_preview={
                "audit_id": result.get("audit_id"),
                "findings_count": result.get("findings_count"),
            },
        )
        duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        ctx.services.supabase.finalize_run(
            run_id_value,
            status="success",
            duration_ms=duration_ms,
        )
        ctx.services.tracing.complete_run(run_id_value)
        return result
    except ValueError as exc:
        emitter.emit_and_persist(
            phase="audit_error",
            message="Product intelligence audit failed",
            level="error",
            error=str(exc),
        )
        duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        ctx.services.supabase.finalize_run(
            run_id_value,
            status="error",
            duration_ms=duration_ms,
            error=str(exc),
        )
        ctx.services.tracing.complete_run(run_id_value)
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        emitter.emit_and_persist(
            phase="audit_error",
            message="Unexpected product intelligence audit failure",
            level="error",
            error=str(exc),
        )
        duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        ctx.services.supabase.finalize_run(
            run_id_value,
            status="error",
            duration_ms=duration_ms,
            error=str(exc),
        )
        ctx.services.tracing.complete_run(run_id_value)
        raise


@router.get(
    "/intelligence/shopify-products",
    summary="Search Shopify products for intelligence audit picker",
)
async def search_shopify_products_for_audit(
    request: Request,
    query: str | None = None,
    limit: int = 25,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, list[dict[str, Any]]]:
    tenant = _require_shop_domain(request, shop_domain)
    safe_limit = max(1, min(limit, 100))
    shopify_client = _resolve_shopify_client(
        ctx=ctx,
        shop_domain=tenant,
        shop_access_token=_resolve_shop_access_token(request),
    )
    products = await shopify_client.list_products_for_audit(
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
    shop_domain = _require_shop_domain(request, data.get("shop_domain"))
    shop_access_token = _resolve_shop_access_token(request, data.get("shop_access_token"))
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
    request: Request,
    limit: int = 50,
    offset: int = 0,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, list[dict[str, Any]]]:
    from application.use_cases.intelligence_list_audits import execute as list_audits_execute

    tenant = _require_shop_domain(request, shop_domain)
    audits = list_audits_execute(
        supabase=ctx.services.supabase,
        shop_domain=tenant,
        limit=limit,
        offset=offset,
    )
    return {"audits": audits}


@router.get("/intelligence/audits/{audit_id}", summary="Get intelligence audit")
async def get_product_intelligence_audit(
    request: Request,
    audit_id: str,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.intelligence_get_audit import execute as get_audit_execute

    tenant = _require_shop_domain(request, shop_domain)
    audit = get_audit_execute(
        supabase=ctx.services.supabase,
        audit_id=audit_id,
        shop_domain=tenant,
    )
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")
    return {"audit": audit}


@router.get(
    "/intelligence/audits/{audit_id}/suggestions",
    summary="List intelligence suggestions for an audit",
)
async def list_product_intelligence_suggestions(
    request: Request,
    audit_id: str,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, list[dict[str, Any]]]:
    from application.use_cases.intelligence_list_suggestions import (
        execute as list_suggestions_execute,
    )

    tenant = _require_shop_domain(request, shop_domain)
    suggestions = list_suggestions_execute(
        supabase=ctx.services.supabase,
        audit_id=audit_id,
        shop_domain=tenant,
    )
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
    shop_domain = _require_shop_domain(request, data.get("shop_domain"))
    shop_access_token = _resolve_shop_access_token(request, data.get("shop_access_token"))
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
            shop_domain=shop_domain,
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
    shop_domain = _require_shop_domain(request, data.get("shop_domain"))
    shop_access_token = _resolve_shop_access_token(request, data.get("shop_access_token"))
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
            shop_domain=shop_domain,
        )
        suggestion = result.get("suggestion")
        if isinstance(suggestion, dict):
            result["suggestion"] = _with_reversibility_flags(suggestion)
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))



@router.get("/intelligence/normalization-settings", summary="Get normalization settings")
async def get_product_intelligence_normalization_settings(
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = _require_shop_domain(request, shop_domain)
    stored = ctx.services.supabase.get_product_intelligence_normalization_settings(
        shop_domain=tenant,
    )
    fallback = _default_normalization_settings(request)
    normalized = _coerce_normalization_settings(stored or {}, fallback=fallback)
    updated_at = stored.get("updated_at") if isinstance(stored, dict) else None
    return {
        "settings": {
            **normalized,
            "updated_at": updated_at,
        }
    }


@router.post("/intelligence/normalization-settings", summary="Upsert normalization settings")
async def upsert_product_intelligence_normalization_settings(
    request: Request,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
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

    settings_json = payload.get("settings_json")
    if isinstance(settings_json, str) and settings_json.strip():
        try:
            parsed_settings = json.loads(settings_json)
            if isinstance(parsed_settings, dict):
                payload["settings"] = parsed_settings
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid settings_json: {exc}")

    tenant = _require_shop_domain(request, payload.get("shop_domain"))
    settings_payload = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
    current = ctx.services.supabase.get_product_intelligence_normalization_settings(
        shop_domain=tenant,
    )
    fallback = _coerce_normalization_settings(
        current or {},
        fallback=_default_normalization_settings(request),
    )
    normalized = _coerce_normalization_settings(settings_payload, fallback=fallback)
    saved = ctx.services.supabase.upsert_product_intelligence_normalization_settings(
        shop_domain=tenant,
        settings=normalized,
    )
    return {
        "settings": {
            **normalized,
            "updated_at": saved.get("updated_at") if isinstance(saved, dict) else None,
        }
    }


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
    shop_domain = _require_shop_domain(request, payload.get("shop_domain"))
    shop_access_token = _resolve_shop_access_token(request, payload.get("shop_access_token"))
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
                shop_domain=shop_domain,
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
