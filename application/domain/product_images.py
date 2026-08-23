"""Safe product-image resolution for review previews and Shopify submission."""

from __future__ import annotations

import ipaddress
from copy import deepcopy
from typing import Any, Literal, TypedDict
from urllib.parse import quote, urlsplit


class ResolvedProductImage(TypedDict):
    url: str
    alt_text: str | None
    origin: Literal["extracted", "shopify"]


_RESERVED_HOSTS = {
    "example.com",
    "example.net",
    "example.org",
    "invalid",
    "localhost",
    "test",
}


def public_https_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
        hostname = (parsed.hostname or "").rstrip(".").lower()
        if (
            parsed.scheme.lower() != "https"
            or not hostname
            or parsed.username
            or parsed.password
        ):
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


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def resolve_extracted_image(product: dict[str, Any]) -> ResolvedProductImage | None:
    media_gallery = product.get("media_gallery")
    if isinstance(media_gallery, list):
        selected = sorted(
            (
                item
                for item in media_gallery
                if isinstance(item, dict)
                and item.get("included") is True
                and item.get("validation_status") == "ready"
                and item.get("asset_id")
            ),
            key=lambda item: int(item.get("position") or 0),
        )
        if selected:
            item = selected[0]
            asset_id = quote(str(item["asset_id"]), safe="")
            return {
                "url": f"/api/media-assets/{asset_id}",
                "alt_text": _text(item.get("alt_text")),
                "origin": "extracted",
            }
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
        image = (
            candidate.get("image")
            if isinstance(candidate.get("image"), dict)
            else candidate
        )
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
    _ = non_production  # Kept for call-site compatibility; fake media is forbidden.
    extracted = resolve_extracted_image(product)
    if extracted:
        return extracted
    shopify = resolve_shopify_image(live)
    if shopify:
        return shopify
    return None


def review_media_gallery(
    product: dict[str, Any], live: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Return safe, merchant-facing existing and incoming gallery rows."""

    gallery: list[dict[str, Any]] = []
    if isinstance(live, dict):
        media = live.get("media")
        nodes = media.get("nodes") if isinstance(media, dict) else []
        for position, node in enumerate(nodes if isinstance(nodes, list) else [], 1):
            if not isinstance(node, dict) or not node.get("id"):
                continue
            image = node.get("image") if isinstance(node.get("image"), dict) else {}
            gallery.append(
                {
                    "review_media_id": str(node["id"]),
                    "shopify_media_id": str(node["id"]),
                    "source_kind": "shopify_existing",
                    "validation_status": "ready",
                    "assignment_status": "assigned",
                    "included": True,
                    "locked": True,
                    "position": position,
                    "preview_url": public_https_url(image.get("url")),
                    "alt_text": _text(image.get("altText")),
                }
            )
    incoming = product.get("media_gallery")
    if isinstance(incoming, list):
        for raw in incoming:
            if not isinstance(raw, dict):
                continue
            item = deepcopy(raw)
            if item.get("asset_id"):
                item["preview_url"] = (
                    f"/api/media-assets/{quote(str(item['asset_id']), safe='')}"
                )
            gallery.append(item)
    elif legacy := resolve_extracted_image(
        {k: v for k, v in product.items() if k != "media_gallery"}
    ):
        # Older drafts can still be reviewed. Their external URL is visibly
        # marked legacy and will be fetched/validated again before submission.
        gallery.append(
            {
                "review_media_id": f"legacy:{legacy['url']}",
                "source_kind": "legacy_external_url",
                "source_url": legacy["url"],
                "preview_url": legacy["url"],
                "validation_status": "ready",
                "assignment_status": "assigned",
                "included": True,
                "locked": False,
                "position": len(gallery) + 1,
                "alt_text": legacy.get("alt_text"),
            }
        )
    return gallery


def approved_media_gallery(item: dict[str, Any]) -> list[dict[str, Any]]:
    gallery = item.get("media_gallery")
    if not isinstance(gallery, list):
        return []
    approved: list[dict[str, Any]] = []
    for row in sorted(
        (entry for entry in gallery if isinstance(entry, dict)),
        key=lambda entry: int(entry.get("position") or 0),
    ):
        if row.get("source_kind") == "shopify_existing":
            if row.get("shopify_media_id"):
                approved.append(
                    {
                        "source_kind": "shopify_existing",
                        "shopify_media_id": str(row["shopify_media_id"]),
                        "position": int(row.get("position") or len(approved) + 1),
                        "alt_text": _text(row.get("alt_text")),
                    }
                )
            continue
        if row.get("included") is not True or row.get("validation_status") != "ready":
            continue
        approved.append(
            {
                key: deepcopy(row[key])
                for key in (
                    "review_media_id",
                    "asset_id",
                    "source_kind",
                    "source_url",
                    "content_sha256",
                    "content_type",
                    "position",
                    "alt_text",
                )
                if key in row
            }
        )
    return approved


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
