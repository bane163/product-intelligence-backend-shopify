from __future__ import annotations

import re
from typing import Any

from application.ports.document_layout_port import DocumentAnchor, DocumentLayout
from application.domain.shopify_mutation_plan import _normalized_path_part, _schema_from_product, _variant_subject


def _normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def verify_pdf_source_refs(
    products: list[dict[str, Any]], layout: DocumentLayout, source_file_id: str
) -> tuple[int, int]:
    """Replace model locations with trusted anchors and discard ambiguous evidence."""
    resolved_count = dropped_count = 0
    for product in products:
        incoming = product.get("source_refs")
        refs = [ref for ref in incoming if isinstance(ref, dict)] if isinstance(incoming, list) else []
        verified: list[dict[str, Any]] = []
        product_page: int | None = None
        fields = [(field, product.get(field), field) for field in ("title", "vendor")]
        option_names = _schema_from_product(product)
        variants = product.get("variants") if isinstance(product.get("variants"), list) else []
        for variant in (item for item in variants if isinstance(item, dict)):
            prefix = f"variant[{_normalized_path_part(_variant_subject(variant, option_names))}]"
            fields.extend((field, variant.get(field), f"{prefix}.{field}") for field in ("sku", "price"))
        for field, expected, value_path in fields:
            needle = _normalized(expected)
            if not needle:
                continue
            candidate_ref = next((ref for ref in refs if str(ref.get("value_path") or "") == value_path), None)
            if candidate_ref is None and value_path == field:
                legacy = [ref for ref in refs if not ref.get("value_path") and str(ref.get("field") or "").casefold() == field]
                candidate_ref = legacy[0] if len(legacy) == 1 else None
            anchor = layout.resolve(str(candidate_ref.get("anchor_id") or "")) if candidate_ref else None
            if anchor is not None and needle not in _normalized(anchor.text):
                anchor = None
            if anchor is None:
                candidates = [item for item in layout.anchors if needle in _normalized(item.text)]
                if field != "title" and product_page is not None:
                    candidates = [item for item in candidates if item.page == product_page]
                if len(candidates) == 1:
                    anchor = candidates[0]
            if anchor is None:
                if candidate_ref is not None:
                    dropped_count += 1
                continue
            if field == "title":
                product_page = anchor.page
            verified.append({
                "source_file_id": source_file_id,
                "field": field,
                "value_path": value_path,
                "document_kind": "pdf",
                "anchor_id": anchor.id,
                "page": anchor.page,
                "bbox": list(anchor.bbox),
                "value": str(expected),
                "source_provider": anchor.provider,
            })
            resolved_count += 1
        product["source_refs"] = verified
    return resolved_count, dropped_count
