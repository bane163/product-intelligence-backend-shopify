"""Shared helpers for agent routers."""

import json
import logging
import os
from typing import Any

from fastapi import HTTPException, Request

import shopify_session_store

LOG = logging.getLogger(__name__)
DEV_BILLING_SIMULATOR_PLAN_HEADER = "x-dev-billing-simulator-plan"
DEV_BILLING_SIMULATOR_PLANS = {"starter", "growth", "scale"}


def parse_products_json(products_json: str) -> list[dict[str, Any]]:
    """Parse product payloads provided as JSON strings."""
    try:
        parsed = json.loads(products_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid products JSON: {exc}")

    if isinstance(parsed, dict) and isinstance(parsed.get("products"), list):
        return [item for item in parsed["products"] if isinstance(item, dict)]

    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]

    raise HTTPException(
        status_code=400,
        detail="products_json must be a list or {'products': [...]}",
    )


def normalize_shop_domain(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def resolve_shop_domain(request: Request, candidate: Any = None) -> str | None:
    sources = [
        normalize_shop_domain(request.headers.get("x-shop-domain")),
        normalize_shop_domain(request.headers.get("x-shopify-shop-domain")),
        normalize_shop_domain(candidate),
        normalize_shop_domain(request.query_params.get("shop_domain")),
        normalize_shop_domain(request.query_params.get("shop")),
    ]
    resolved = [value for value in sources if value]
    if not resolved:
        return None
    first = resolved[0]
    if any(value != first for value in resolved[1:]):
        raise HTTPException(status_code=403, detail="shop_domain mismatch")
    return first


def require_shop_domain(request: Request, candidate: Any = None) -> str:
    shop_domain = resolve_shop_domain(request, candidate)
    if not shop_domain:
        raise HTTPException(status_code=400, detail="Missing shop_domain")
    return shop_domain


def require_authenticated_shop_domain(request: Request, candidate: Any = None) -> str:
    header_shop_domain = resolve_shop_domain(
        request,
        normalize_shop_domain(request.headers.get("x-shop-domain"))
        or normalize_shop_domain(request.headers.get("x-shopify-shop-domain")),
    )
    if not header_shop_domain:
        raise HTTPException(status_code=400, detail="Missing authenticated shop_domain header")

    body_shop_domain = normalize_shop_domain(candidate)
    if body_shop_domain and body_shop_domain != header_shop_domain:
        raise HTTPException(status_code=403, detail="shop_domain mismatch")

    return header_shop_domain


def _load_stored_shop_access_token(shop_domain: str | None) -> str | None:
    if not shop_domain:
        return None
    try:
        return shopify_session_store.get_offline_access_token(shop_domain)
    except Exception as exc:
        LOG.warning(
            "Stored shop token lookup unavailable for shop=%s error=%s",
            shop_domain,
            exc,
        )
        return None


def resolve_shop_access_token(
    request: Request,
    candidate: Any = None,
    *,
    shop_domain: str | None = None,
) -> str | None:
    if shop_domain:
        require_internal_service_key(request)
    forwarded = request.headers.get("x-shop-access-token")
    if isinstance(forwarded, str) and forwarded.strip():
        return forwarded.strip()
    if not os.getenv("INTERNAL_SERVICE_KEY", "").strip() and _current_environment_name() != "production":
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    stored_token = _load_stored_shop_access_token(shop_domain)
    return stored_token


def require_internal_service_key(request: Request) -> None:
    expected = os.getenv("INTERNAL_SERVICE_KEY", "").strip()
    if not expected and _current_environment_name() != "production":
        return
    supplied = request.headers.get("x-stockpile-service-key", "").strip()
    if not expected or not supplied or not __import__("hmac").compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="Invalid service authentication")


def _current_environment_name() -> str:
    for key in ("NODE_ENV", "APP_ENV", "ENVIRONMENT"):
        value = os.getenv(key)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized:
                return normalized
    return ""


def resolve_dev_billing_simulator_plan(request: Request) -> str | None:
    if _current_environment_name() == "production":
        return None

    raw_plan = request.headers.get(DEV_BILLING_SIMULATOR_PLAN_HEADER)
    if not isinstance(raw_plan, str):
        return None

    normalized_plan = raw_plan.strip().lower()
    if normalized_plan in DEV_BILLING_SIMULATOR_PLANS:
        return normalized_plan
    return None
