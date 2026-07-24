"""Verify canonical review-value evidence against trusted PDF layout anchors."""

from __future__ import annotations

import re
from typing import Any

from application.domain.review_value_paths import (
    ReviewValue,
    canonical_value_path,
    is_numeric_field,
    iter_review_values,
    values_equal,
)
from application.ports.document_layout_port import DocumentAnchor, DocumentLayout


def _normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value if value is not None else "").casefold())


def _anchor_matches(anchor: DocumentAnchor, item: ReviewValue) -> bool:
    if values_equal(anchor.text, item.value, numeric_strings=is_numeric_field(item.field)):
        return True
    # Layout anchors frequently include a label beside an exact scalar value.
    if isinstance(item.value, str):
        needle = _normalized(item.value)
        return bool(needle) and needle in _normalized(anchor.text)
    return False


def _origin_for(product: dict[str, Any], path: str) -> str | None:
    origins = product.get("value_origins") or product.get("_value_origins")
    if not isinstance(origins, dict):
        return None
    matches = {str(origin) for key, origin in origins.items() if canonical_value_path(key) == canonical_value_path(path)}
    return next(iter(matches)) if len(matches) == 1 else None


def verify_pdf_source_refs(
    products: list[dict[str, Any]], layout: DocumentLayout, source_file_id: str
) -> tuple[int, int]:
    """Replace model locations with resolved anchors and omit ambiguous evidence."""
    resolved_count = dropped_count = 0
    for product in products:
        incoming = product.get("source_refs")
        refs = [ref for ref in incoming if isinstance(ref, dict)] if isinstance(incoming, list) else []
        verified: list[dict[str, Any]] = []
        product_page: int | None = None
        for item in iter_review_values(product):
            if _origin_for(product, item.value_path) in {"ai_assisted", "derived", "merchant_edited", "unknown", "defaulted"}:
                continue
            candidates_refs = [
                ref for ref in refs
                if canonical_value_path(ref.get("value_path")) == canonical_value_path(item.value_path)
            ]
            if not candidates_refs and item.scope == "product":
                candidates_refs = [
                    ref for ref in refs
                    if not ref.get("value_path") and canonical_value_path(ref.get("field")) == canonical_value_path(item.field)
                ]

            if len(candidates_refs) > 1:
                dropped_count += len(candidates_refs)
                continue

            anchors: dict[str, DocumentAnchor] = {}
            for ref in candidates_refs:
                anchor = layout.resolve(str(ref.get("anchor_id") or ""))
                if anchor is not None and _anchor_matches(anchor, item):
                    anchors[anchor.id] = anchor
                else:
                    dropped_count += 1
            if not anchors and not candidates_refs:
                matches = [anchor for anchor in layout.anchors if _anchor_matches(anchor, item)]
                if item.field != "title" and product_page is not None:
                    matches = [anchor for anchor in matches if anchor.page == product_page]
                if len(matches) == 1:
                    anchors[matches[0].id] = matches[0]
            if len(anchors) != 1:
                if candidates_refs and anchors:
                    dropped_count += len(candidates_refs)
                continue
            anchor = next(iter(anchors.values()))
            if item.field == "title":
                product_page = anchor.page
            verified.append({
                "source_file_id": source_file_id,
                "field": item.field,
                "value_path": item.value_path,
                "document_kind": "pdf",
                "anchor_id": anchor.id,
                "page": anchor.page,
                "bbox": list(anchor.bbox),
                "value": str(item.value),
                "source_provider": anchor.provider,
            })
            resolved_count += 1
        product["source_refs"] = verified
    return resolved_count, dropped_count
