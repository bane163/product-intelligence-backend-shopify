"""Shared helpers for agent routers."""

import json
from typing import Any

from fastapi import HTTPException


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

    raise HTTPException(status_code=400, detail="products_json must be a list or {'products': [...]}") 
