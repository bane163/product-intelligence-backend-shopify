"""Discover, validate, and persist reviewable product images.

The extraction model may return external URLs, while PDFs can contain the real
image bytes without a usable URL.  This module snapshots both sources into the
tenant-owned private file store so review and submission use the exact bytes a
merchant saw.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
from io import BytesIO
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit
import uuid

import fitz
import httpx
from PIL import Image, ImageOps

from application.domain.product_images import public_https_url


PRODUCT_MEDIA_FILE_ORIGIN = "product_media_asset"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_DIMENSION = 4_472
MAX_IMAGE_ASPECT_RATIO = 100
MAX_REDIRECTS = 4
MAX_MEDIA_CANDIDATES = 100
SUPPORTED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


class ProductMediaFetchError(ValueError):
    """A merchant-facing image validation failure with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalized_rect(rect: fitz.Rect, page: fitz.Page) -> list[float]:
    width = max(float(page.rect.width), 1.0)
    height = max(float(page.rect.height), 1.0)
    return [
        round(max(0.0, min(1.0, float(rect.x0) / width)), 6),
        round(max(0.0, min(1.0, float(rect.y0) / height)), 6),
        round(max(0.0, min(1.0, float(rect.x1) / width)), 6),
        round(max(0.0, min(1.0, float(rect.y1) / height)), 6),
    ]


def _normalize_image(content: bytes) -> tuple[bytes, str, str, int, int]:
    if len(content) > MAX_IMAGE_BYTES:
        raise ProductMediaFetchError(
            "IMAGE_TOO_LARGE", "The image exceeds the 20 MB limit."
        )
    try:
        with Image.open(BytesIO(content)) as source:
            if getattr(source, "n_frames", 1) != 1 or bool(
                getattr(source, "is_animated", False)
            ):
                raise ProductMediaFetchError(
                    "ANIMATED_IMAGE_UNSUPPORTED",
                    "Animated or multi-frame images are not supported.",
                )
            source_width, source_height = source.size
            aspect_ratio = max(
                source_width / max(source_height, 1),
                source_height / max(source_width, 1),
            )
            if (
                source_width < 1
                or source_height < 1
                or source_width > MAX_IMAGE_DIMENSION
                or source_height > MAX_IMAGE_DIMENSION
                or source_width * source_height > MAX_IMAGE_PIXELS
                or aspect_ratio > MAX_IMAGE_ASPECT_RATIO
            ):
                raise ProductMediaFetchError(
                    "IMAGE_DIMENSIONS_INVALID",
                    "The image exceeds Shopify's product-image dimensions or aspect-ratio limits.",
                )
            source.load()
            displayed = ImageOps.exif_transpose(source)
            width, height = displayed.size
            has_alpha = (
                displayed.mode in {"RGBA", "LA"} or "transparency" in displayed.info
            )
            output = BytesIO()
            if has_alpha:
                displayed.convert("RGBA").save(output, format="PNG", optimize=True)
                mime_type, extension = "image/png", "png"
            elif (source.format or "").upper() == "WEBP":
                displayed.convert("RGB").save(
                    output, format="WEBP", quality=92, method=6
                )
                mime_type, extension = "image/webp", "webp"
            else:
                displayed.convert("RGB").save(
                    output, format="JPEG", quality=92, optimize=True, progressive=True
                )
                mime_type, extension = "image/jpeg", "jpg"
            normalized = output.getvalue()
            if len(normalized) > MAX_IMAGE_BYTES:
                raise ProductMediaFetchError(
                    "IMAGE_TOO_LARGE", "The normalized image exceeds the 20 MB limit."
                )
            return normalized, mime_type, extension, width, height
    except ProductMediaFetchError:
        raise
    except Exception as exc:
        raise ProductMediaFetchError(
            "IMAGE_DECODE_FAILED", "The image file is corrupt or unsupported."
        ) from exc


async def _validate_public_host(url: str) -> str:
    candidate = public_https_url(url)
    if candidate is None:
        raise ProductMediaFetchError(
            "IMAGE_URL_INVALID",
            "Use a public HTTPS image URL; placeholder and private addresses are not allowed.",
        )
    parsed = urlsplit(candidate)
    hostname = parsed.hostname or ""
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            parsed.port or 443,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ProductMediaFetchError(
            "IMAGE_HOST_UNREACHABLE", "The image host could not be resolved."
        ) from exc
    resolved = {
        item[4][0]
        for item in addresses
        if isinstance(item, tuple)
        and len(item) >= 5
        and isinstance(item[4], tuple)
        and item[4]
    }
    if not resolved:
        raise ProductMediaFetchError(
            "IMAGE_HOST_UNREACHABLE", "The image host did not resolve to an address."
        )
    try:
        if any(not ipaddress.ip_address(address).is_global for address in resolved):
            raise ProductMediaFetchError(
                "IMAGE_URL_PRIVATE_ADDRESS",
                "Private, loopback, and link-local image hosts are not allowed.",
            )
    except ValueError as exc:
        raise ProductMediaFetchError(
            "IMAGE_HOST_UNREACHABLE", "The image host returned an invalid address."
        ) from exc
    return candidate


async def fetch_external_image(url: str) -> tuple[bytes, str, str, int, int, str]:
    """Fetch one public image while validating every redirect target."""

    current = url
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(10.0, connect=5.0),
        trust_env=False,
        headers={"Accept": "image/jpeg,image/png,image/webp"},
    ) as client:
        for redirect_count in range(MAX_REDIRECTS + 1):
            current = await _validate_public_host(current)
            async with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    if redirect_count >= MAX_REDIRECTS:
                        raise ProductMediaFetchError(
                            "IMAGE_REDIRECT_LIMIT",
                            "The image URL redirected too many times.",
                        )
                    location = response.headers.get("location")
                    if not location:
                        raise ProductMediaFetchError(
                            "IMAGE_REDIRECT_INVALID",
                            "The image redirect has no destination.",
                        )
                    current = urljoin(current, location)
                    continue
                if response.status_code != 200:
                    raise ProductMediaFetchError(
                        "IMAGE_DOWNLOAD_FAILED",
                        f"The image server returned HTTP {response.status_code}.",
                    )
                declared_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].lower()
                )
                if declared_type and declared_type not in SUPPORTED_IMAGE_MIME_TYPES:
                    raise ProductMediaFetchError(
                        "IMAGE_CONTENT_TYPE_INVALID",
                        "The URL did not return a supported JPEG, PNG, or WebP image.",
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise ProductMediaFetchError(
                            "IMAGE_TOO_LARGE", "The image exceeds the 20 MB limit."
                        )
                    chunks.append(chunk)
                normalized = _normalize_image(b"".join(chunks))
                return (*normalized, current)
    raise ProductMediaFetchError(
        "IMAGE_DOWNLOAD_FAILED", "The image could not be downloaded."
    )


def _asset_id(
    *, source_file_id: str, source_key: str, digest: str, extension: str
) -> str:
    stable = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"stockpile-product-media:{source_file_id}:{source_key}:{digest}",
    )
    return f"product-media-{stable}.{extension}"


def _save_asset(
    *,
    supabase: Any,
    shop_domain: str,
    source_file_id: str,
    source_key: str,
    content: bytes,
    mime_type: str,
    extension: str,
) -> tuple[str, str]:
    digest = hashlib.sha256(content).hexdigest()
    asset_id = _asset_id(
        source_file_id=source_file_id,
        source_key=source_key,
        digest=digest,
        extension=extension,
    )
    supabase.file.save_file(
        asset_id,
        name=asset_id,
        content=content,
        content_type=mime_type,
        file_origin=PRODUCT_MEDIA_FILE_ORIGIN,
        shop_domain=shop_domain,
    )
    return asset_id, digest


def _product_page_context(
    products: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    context: dict[int, list[dict[str, Any]]] = {}
    for product_index, product in enumerate(products):
        refs = (
            product.get("source_refs")
            if isinstance(product.get("source_refs"), list)
            else []
        )
        by_page: dict[int, list[dict[str, Any]]] = {}
        for ref in refs:
            if not isinstance(ref, dict) or not isinstance(ref.get("page"), int):
                continue
            bbox = ref.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            by_page.setdefault(int(ref["page"]), []).append(ref)
        for page_number, page_refs in by_page.items():
            title_ref = next(
                (
                    ref
                    for ref in page_refs
                    if str(ref.get("value_path") or ref.get("field") or "") == "title"
                ),
                None,
            )
            anchor_y = float((title_ref or page_refs[0])["bbox"][1])
            context.setdefault(page_number, []).append(
                {
                    "product_index": product_index,
                    "anchor_y": anchor_y,
                    "refs": page_refs,
                }
            )
    for entries in context.values():
        entries.sort(key=lambda item: (item["anchor_y"], item["product_index"]))
    return context


def _assign_product(
    *,
    page_number: int,
    bbox: list[float],
    page_context: dict[int, list[dict[str, Any]]],
) -> tuple[int | None, str]:
    entries = page_context.get(page_number, [])
    if not entries:
        return None, "unassigned"
    if len(entries) == 1:
        return int(entries[0]["product_index"]), "assigned"
    center_y = (bbox[1] + bbox[3]) / 2.0
    starts = [max(0.0, float(item["anchor_y"]) - 0.02) for item in entries]
    for index, entry in enumerate(entries):
        end = starts[index + 1] if index + 1 < len(starts) else 1.0
        if starts[index] <= center_y < end:
            return int(entry["product_index"]), "assigned"
    nearest = min(entries, key=lambda item: abs(float(item["anchor_y"]) - center_y))
    return int(nearest["product_index"]), "ambiguous"


def _embedded_candidates(
    *, pdf_content: bytes, products: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    page_context = _product_page_context(products)
    candidates: list[dict[str, Any]] = []
    document = fitz.open(stream=pdf_content, filetype="pdf")
    try:
        for page_index in range(document.page_count):
            page = document[page_index]
            seen_rects: set[tuple[int, tuple[float, ...]]] = set()
            for image_info in page.get_images(full=True):
                xref = int(image_info[0])
                extracted = document.extract_image(xref)
                raw = extracted.get("image") if isinstance(extracted, dict) else None
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    content, mime_type, extension, width, height = _normalize_image(
                        bytes(raw)
                    )
                except ProductMediaFetchError:
                    continue
                digest = hashlib.sha256(content).hexdigest()
                for rect in page.get_image_rects(xref):
                    bbox = _normalized_rect(rect, page)
                    identity = (xref, tuple(bbox))
                    if identity in seen_rects:
                        continue
                    seen_rects.add(identity)
                    page_number = page_index + 1
                    product_index, assignment = _assign_product(
                        page_number=page_number, bbox=bbox, page_context=page_context
                    )
                    area = max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
                    aspect = max(width / max(height, 1), height / max(width, 1))
                    candidates.append(
                        {
                            "source_key": f"pdf:{page_number}:{xref}:{','.join(map(str, bbox))}",
                            "source_kind": "embedded_pdf",
                            "content": content,
                            "mime_type": mime_type,
                            "extension": extension,
                            "content_sha256": digest,
                            "width": width,
                            "height": height,
                            "page": page_number,
                            "bbox": bbox,
                            "product_index": product_index,
                            "assignment_status": assignment,
                            "probable_decoration": width < 64
                            or height < 64
                            or area < 0.005
                            or aspect > 8,
                        }
                    )
                    if len(candidates) >= MAX_MEDIA_CANDIDATES:
                        return candidates
    finally:
        document.close()
    page_sets: dict[str, set[int]] = {}
    for candidate in candidates:
        page_sets.setdefault(candidate["content_sha256"], set()).add(candidate["page"])
    for candidate in candidates:
        if len(page_sets.get(candidate["content_sha256"], set())) >= 2:
            candidate["probable_decoration"] = True
    return candidates


def _external_sources(product: dict[str, Any]) -> list[tuple[str, str | None, int]]:
    sources: list[tuple[str, str | None, int]] = []
    primary = _text(product.get("image_src"))
    if primary:
        sources.append(
            (
                primary,
                _text(product.get("image_alt_text")),
                int(product.get("image_position") or 1),
            )
        )
    images = product.get("images")
    if isinstance(images, list):
        for index, image in enumerate(images):
            if not isinstance(image, dict) or not _text(image.get("src")):
                continue
            sources.append(
                (
                    str(image["src"]).strip(),
                    _text(image.get("alt") or image.get("alt_text")),
                    int(image.get("position") or index + 1),
                )
            )
    deduped: list[tuple[str, str | None, int]] = []
    seen: set[str] = set()
    for source in sorted(sources, key=lambda item: item[2]):
        if source[0] not in seen:
            seen.add(source[0])
            deduped.append(source)
    return deduped


async def create_external_media_item(
    *,
    supabase: Any,
    source_file_id: str,
    shop_domain: str,
    source_url: str,
    alt_text: str | None,
    position: int,
    source_key: str | None = None,
) -> dict[str, Any]:
    """Create one tenant-owned gallery row from a merchant-provided URL."""

    stable_key = source_key or f"url:{source_url}"
    try:
        content, mime_type, extension, width, height, final_url = (
            await fetch_external_image(source_url)
        )
        asset_id, digest = _save_asset(
            supabase=supabase,
            shop_domain=shop_domain,
            source_file_id=source_file_id,
            source_key=stable_key,
            content=content,
            mime_type=mime_type,
            extension=extension,
        )
        return {
            "review_media_id": str(uuid.uuid5(uuid.NAMESPACE_URL, asset_id)),
            "asset_id": asset_id,
            "source_kind": "external_url",
            "source_url": source_url,
            "resolved_url": final_url,
            "validation_status": "ready",
            "assignment_status": "assigned",
            "included": True,
            "position": position,
            "alt_text": _text(alt_text),
            "content_sha256": digest,
            "content_type": mime_type,
            "width": width,
            "height": height,
        }
    except ProductMediaFetchError as exc:
        code, message = exc.code, str(exc)
    except Exception:
        code, message = (
            "IMAGE_DOWNLOAD_FAILED",
            "The image could not be downloaded or validated.",
        )
    return {
        "review_media_id": str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"{source_file_id}:{stable_key}")
        ),
        "source_kind": "external_url",
        "source_url": source_url,
        "validation_status": "invalid",
        "assignment_status": "assigned",
        "included": False,
        "position": position,
        "alt_text": _text(alt_text),
        "issue_code": code,
        "issue_message": message,
    }


async def attach_product_media(
    *,
    supabase: Any,
    products: list[dict[str, Any]],
    source_file_id: str,
    shop_domain: str,
    pdf_content: bytes | None = None,
) -> dict[str, int]:
    """Attach a persisted, reviewable media gallery to each extracted product."""

    galleries: list[list[dict[str, Any]]] = [[] for _ in products]
    embedded = (
        _embedded_candidates(pdf_content=pdf_content, products=products)
        if pdf_content
        else []
    )
    for candidate in embedded:
        product_index = candidate.get("product_index")
        if not isinstance(product_index, int) or not (
            0 <= product_index < len(products)
        ):
            continue
        asset_id, digest = _save_asset(
            supabase=supabase,
            shop_domain=shop_domain,
            source_file_id=source_file_id,
            source_key=str(candidate["source_key"]),
            content=candidate["content"],
            mime_type=str(candidate["mime_type"]),
            extension=str(candidate["extension"]),
        )
        probable_decoration = bool(candidate.get("probable_decoration"))
        assignment = str(candidate.get("assignment_status") or "unassigned")
        galleries[product_index].append(
            {
                "review_media_id": str(uuid.uuid5(uuid.NAMESPACE_URL, asset_id)),
                "asset_id": asset_id,
                "source_kind": "embedded_pdf",
                "validation_status": "ready",
                "assignment_status": (
                    "probable_decoration" if probable_decoration else assignment
                ),
                "included": assignment == "assigned" and not probable_decoration,
                "position": 0,
                "alt_text": _text(products[product_index].get("title")),
                "page": candidate.get("page"),
                "bbox": candidate.get("bbox"),
                "content_sha256": digest,
                "content_type": candidate.get("mime_type"),
                "width": candidate.get("width"),
                "height": candidate.get("height"),
                **(
                    {
                        "issue_code": "POSSIBLE_DECORATION",
                        "issue_message": "This repeated, small, or unusually shaped image needs manual selection.",
                    }
                    if probable_decoration
                    else {}
                ),
            }
        )

    for product_index, product in enumerate(products):
        for source_index, (source_url, alt_text, position) in enumerate(
            _external_sources(product)
        ):
            source_key = f"url:{source_index}:{source_url}"
            item = await create_external_media_item(
                supabase=supabase,
                source_file_id=source_file_id,
                shop_domain=shop_domain,
                source_url=source_url,
                alt_text=alt_text or _text(product.get("title")),
                position=position,
                source_key=source_key,
            )
            galleries[product_index].append(item)

    ready = invalid = ambiguous = 0
    for product_index, gallery in enumerate(galleries):
        # Explicit source positions lead, followed by embedded reading order.
        gallery.sort(
            key=lambda item: (
                0 if item.get("source_kind") == "external_url" else 1,
                int(item.get("position") or 0),
                int(item.get("page") or 0),
                float((item.get("bbox") or [0, 0])[1]),
                float((item.get("bbox") or [0, 0])[0]),
            )
        )
        seen_hashes: set[str] = set()
        position = 1
        for item in gallery:
            digest = str(item.get("content_sha256") or "")
            if digest and digest in seen_hashes:
                item["included"] = False
                item["assignment_status"] = "duplicate"
                item["issue_code"] = "DUPLICATE_IMAGE"
                item["issue_message"] = "This image duplicates another gallery item."
            elif digest:
                seen_hashes.add(digest)
            if item.get("included"):
                item["position"] = position
                position += 1
            if item.get("validation_status") == "ready":
                ready += 1
            else:
                invalid += 1
            if item.get("assignment_status") not in {"assigned"}:
                ambiguous += 1
        products[product_index]["media_gallery"] = gallery
    return {
        "ready": ready,
        "invalid": invalid,
        "ambiguous": ambiguous,
        "embedded": len(embedded),
    }
