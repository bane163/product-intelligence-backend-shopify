"""Shared helpers for agent routers."""

import json
from typing import Any

from fastapi import HTTPException, Request


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
    header_shop = normalize_shop_domain(request.headers.get("x-shop-domain"))
    body_shop = normalize_shop_domain(candidate)
    if header_shop and body_shop and header_shop != body_shop:
        raise HTTPException(status_code=403, detail="shop_domain mismatch")
    return header_shop or body_shop


def require_shop_domain(request: Request, candidate: Any = None) -> str:
    shop_domain = resolve_shop_domain(request, candidate)
    if not shop_domain:
        raise HTTPException(status_code=400, detail="Missing shop_domain")
    return shop_domain


def resolve_shop_access_token(request: Request, candidate: Any = None) -> str | None:
    header_token = request.headers.get("x-shop-access-token")
    if isinstance(header_token, str) and header_token.strip():
        return header_token.strip()
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return None
