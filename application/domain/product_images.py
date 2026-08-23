"""Safe product-image resolution for review previews and Shopify submission."""

from __future__ import annotations

import hashlib
import ipaddress
import os
from typing import Any, Literal, TypedDict
from urllib.parse import quote, urlsplit


class ResolvedProductImage(TypedDict):
    url: str
    alt_text: str | None
    origin: Literal["extracted", "shopify", "development_fallback"]


_RESERVED_HOSTS = {
    "example.com",
    "example.net",
    "example.org",
    "invalid",
    "localhost",
    "test",
}


def is_non_production() -> bool:
    for key in ("NODE_ENV", "APP_ENV", "ENVIRONMENT"):
        value = os.getenv(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower() != "production"
    return True


def public_https_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
        hostname = (parsed.hostname or "").rstrip(".").lower()
        if parsed.scheme.lower() != "https" or not hostname or parsed.username or parsed.password:
            return None
    except (ValueError, TypeError):
        return None
    if (
        hostname in _RESERVED_HOSTS
        or any(hostname.endswith(f".{host}") for host in _RESERVED_HOSTS)
        or hostname.endswith((".local", ".localhost", ".internal", ".test", ".invalid"))
    ):
        return None
    try:
        address = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        address = None
    if address is None and "." not in hostname:
        return None
    if address is not None and not address.is_global:
        return None
    return candidate


def seeded_fallback_url(product: dict[str, Any]) -> str:
    identity = str(product.get("handle") or product.get("title") or "product").strip().casefold()
    seed = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"https://picsum.photos/seed/{quote(seed, safe='')}/800/800"


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def resolve_extracted_image(product: dict[str, Any]) -> ResolvedProductImage | None:
    candidates: list[tuple[Any, Any]] = [
        (product.get("image_src"), product.get("image_alt_text"))
    ]
    images = product.get("images")
    if isinstance(images, list):
        candidates.extend(
            (item.get("src"), item.get("alt") or item.get("alt_text"))
            for item in images
            if isinstance(item, dict)
        )
    seen: set[str] = set()
    for raw_url, raw_alt in candidates:
        url = public_https_url(raw_url)
        if not url or url in seen:
            continue
        seen.add(url)
        return {"url": url, "alt_text": _text(raw_alt), "origin": "extracted"}
    return None


def existing_shopify_files(live: dict[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(live, dict):
        return []
    media = live.get("media")
    nodes = media.get("nodes") if isinstance(media, dict) else None
    if not isinstance(nodes, list):
        return []
    return [
        {"id": str(item["id"])}
        for item in nodes
        if isinstance(item, dict) and item.get("id")
    ]


def resolve_shopify_image(live: dict[str, Any] | None) -> ResolvedProductImage | None:
    if not isinstance(live, dict):
        return None
    featured = live.get("featuredImage")
    media = live.get("media")
    nodes = media.get("nodes") if isinstance(media, dict) else []
    candidates = [featured] + list(nodes if isinstance(nodes, list) else [])
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        image = candidate.get("image") if isinstance(candidate.get("image"), dict) else candidate
        url = public_https_url(image.get("url"))
        if url:
            return {
                "url": url,
                "alt_text": _text(image.get("altText")),
                "origin": "shopify",
            }
    return None


def resolve_product_image(
    product: dict[str, Any], live: dict[str, Any] | None, *, non_production: bool
) -> ResolvedProductImage | None:
    extracted = resolve_extracted_image(product)
    if extracted:
        return extracted
    shopify = resolve_shopify_image(live)
    if shopify:
        return shopify
    if non_production:
        return {
            "url": seeded_fallback_url(product),
            "alt_text": _text(product.get("title")),
            "origin": "development_fallback",
        }
    return None


def product_set_files(
    image: ResolvedProductImage | None, live: dict[str, Any] | None
) -> list[dict[str, str]] | None:
    if not image or image["origin"] == "shopify":
        return None
    files = existing_shopify_files(live)
    incoming: dict[str, str] = {
        "contentType": "IMAGE",
        "originalSource": image["url"],
    }
    if image.get("alt_text"):
        incoming["alt"] = str(image["alt_text"])
    files.append(incoming)
    return files
