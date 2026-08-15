"""Deterministic, request-local evidence indexes for new PDF extractions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import unescape
import re
from typing import Any, Iterable

import fitz

from application.domain.review_value_paths import (
    ReviewValue,
    canonical_value_path,
    iter_review_values,
)
from application.domain.field_derivation_policy import (
    NON_SOURCE_ORIGINS,
    is_protected_field,
)
from application.ports.document_layout_port import DocumentAnchor, DocumentLayout


PROTECTED_VARIANT_ALIASES = {
    "price": ("price",),
    "compareAtPrice": ("compareAtPrice", "compare_at_price", "compare_at"),
    "sku": ("sku",),
    "barcode": ("barcode",),
    "taxable": ("taxable",),
    "taxCode": ("taxCode", "tax_code"),
    "inventoryPolicy": ("inventoryPolicy", "inventory_policy"),
    "inventoryItem.cost": ("cost", "unit_cost", "cost_per_item"),
    "inventoryItem.tracked": ("tracked", "inventory_tracked", "track_inventory"),
    "inventoryItem.requiresShipping": ("requiresShipping", "requires_shipping"),
    "inventoryItem.weight.value": ("weight",),
    "inventoryItem.weight.unit": ("weightUnit", "weight_unit", "variant_weight_unit"),
    "inventory_quantity": ("inventory_quantity", "inventoryQuantity", "inventoryQuantities"),
    "fulfillment_service": ("fulfillment_service", "fulfillmentService"),
    "inventory_tracker": ("inventory_tracker", "inventoryTracker"),
    "grams": ("grams",),
    "variant_tax_code": ("variant_tax_code",),
    "price_international": ("price_international", "international_price"),
    "compare_at_price_international": (
        "compare_at_price_international", "international_compare_at_price",
    ),
}


class SourceEvidenceValidationFailed(RuntimeError):
    code = "SOURCE_EVIDENCE_VALIDATION_FAILED"

    def __init__(self, paths: Iterable[str], feedback: str) -> None:
        self.paths = tuple(sorted(set(paths)))
        self.feedback = feedback
        super().__init__(f"{self.code}: {feedback}")


@dataclass(frozen=True, slots=True)
class EvidenceEntry:
    evidence_index: int
    text: str
    page: int
    bbox: tuple[float, float, float, float]
    anchor_id: str
    provider: str


@dataclass(frozen=True, slots=True)
class EvidenceCatalog:
    entries: tuple[EvidenceEntry, ...]

    def resolve(self, evidence_index: int) -> EvidenceEntry | None:
        if evidence_index < 1 or evidence_index > len(self.entries):
            return None
        entry = self.entries[evidence_index - 1]
        return entry if entry.evidence_index == evidence_index else None

    def to_prompt_text(self) -> str:
        return "\n".join(
            f"{entry.evidence_index}. {entry.text}" for entry in self.entries
        )


@dataclass(frozen=True, slots=True)
class EvidenceValidationResult:
    valid: bool
    affected_paths: tuple[str, ...]
    feedback: str


def _sort_key(anchor: DocumentAnchor) -> tuple[Any, ...]:
    role_priority = {"table_cell": 0, "line": 1, "word": 2}
    return (
        anchor.page,
        round(anchor.bbox[1], 8),
        round(anchor.bbox[0], 8),
        round(anchor.bbox[3], 8),
        round(anchor.bbox[2], 8),
        role_priority.get(anchor.role, 3),
        anchor.id,
    )


def _covered_by(candidate: DocumentAnchor, preferred: DocumentAnchor) -> bool:
    if candidate.page != preferred.page:
        return False
    x0, y0, x1, y1 = candidate.bbox
    px0, py0, px1, py1 = preferred.bbox
    center_x, center_y = (x0 + x1) / 2, (y0 + y1) / 2
    if px0 <= center_x <= px1 and py0 <= center_y <= py1:
        return True
    intersection = max(0.0, min(x1, px1) - max(x0, px0)) * max(
        0.0, min(y1, py1) - max(y0, py0)
    )
    area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return area > 0 and intersection / area >= 0.8


def build_evidence_catalog(layout: DocumentLayout) -> EvidenceCatalog:
    """Prefer cells, then lines, then words not covered by preferred entries."""
    anchors = [anchor for anchor in layout.anchors if anchor.text.strip()]
    cells = sorted(
        (anchor for anchor in anchors if anchor.role == "table_cell"), key=_sort_key
    )
    lines = sorted(
        (anchor for anchor in anchors if anchor.role == "line"), key=_sort_key
    )
    words = sorted(
        (anchor for anchor in anchors if anchor.role == "word"), key=_sort_key
    )
    other = sorted(
        (
            anchor
            for anchor in anchors
            if anchor.role not in {"table_cell", "line", "word"}
        ),
        key=_sort_key,
    )

    selected: list[DocumentAnchor] = list(cells)
    selected.extend(
        line for line in lines if not any(_covered_by(line, cell) for cell in cells)
    )
    preferred_for_words = tuple(selected)
    selected.extend(
        word
        for word in words
        if not any(_covered_by(word, preferred) for preferred in preferred_for_words)
    )
    selected.extend(
        anchor
        for anchor in other
        if not any(_covered_by(anchor, preferred) for preferred in selected)
    )
    ordered = sorted(selected, key=_sort_key)
    return EvidenceCatalog(
        tuple(
            EvidenceEntry(
                evidence_index=index,
                text=anchor.text,
                page=anchor.page,
                bbox=anchor.bbox,
                anchor_id=anchor.id,
                provider=anchor.provider,
            )
            for index, anchor in enumerate(ordered, start=1)
        )
    )


def _origin_for(product: dict[str, Any], path: str) -> str | None:
    origins = product.get("value_origins") or product.get("_value_origins")
    if not isinstance(origins, dict):
        return None
    matches = {
        str(origin)
        for key, origin in origins.items()
        if canonical_value_path(key) == canonical_value_path(path)
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _signature(path: Any) -> tuple[str, str] | None:
    match = re.fullmatch(r"([^\[\]]+)\[.*\]\.(.+)", canonical_value_path(path))
    return (match.group(1), match.group(2)) if match else None


def _field_matches_item(field: Any, item: ReviewValue) -> bool:
    normalized = canonical_value_path(field)
    item_field = canonical_value_path(item.field)
    return normalized in {
        "",
        item_field,
        canonical_value_path(item.value_path),
    } or normalized.endswith(f".{item_field}")


def _is_structural_scaffolding_ref(path: str) -> bool:
    """Ignore model locators for structure that is not a review value."""
    return bool(
        re.fullmatch(r"option\[[^\]]+\]\.name", path)
        or re.fullmatch(r"variant\[[^\]]+\]\.option[123]", path)
    )


def _canonical_ref_paths(
    refs: list[dict[str, Any]], items: list[ReviewValue]
) -> tuple[dict[int, str], set[str]]:
    """Canonicalize an unambiguous structured model path without using its value."""
    item_paths = {
        canonical_value_path(item.value_path): item.value_path for item in items
    }
    canonical: dict[int, str] = {}
    mismatched: set[str] = set()
    for position, ref in enumerate(refs):
        raw_path = ref.get("value_path")
        path = canonical_value_path(raw_path)
        if _is_structural_scaffolding_ref(path):
            continue
        if path in item_paths:
            canonical[position] = item_paths[path]
            continue
        if not raw_path and ref.get("field"):
            field_path = canonical_value_path(ref.get("field"))
            matches = [
                item
                for item in items
                if item.scope == "product"
                and canonical_value_path(item.field) == field_path
            ]
        else:
            signature = _signature(path)
            matches = [
                item
                for item in items
                if signature is not None
                and _signature(item.value_path) == signature
                and _field_matches_item(ref.get("field"), item)
            ]
        if len(matches) == 1:
            canonical[position] = matches[0].value_path
        else:
            mismatched.add(
                path or str(raw_path or ref.get("field") or "<missing-value-path>")
            )
    return canonical, mismatched


def _numeric_index(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def validate_indexed_pdf_evidence(
    products: list[dict[str, Any]], catalog: EvidenceCatalog
) -> EvidenceValidationResult:
    affected: set[str] = set()
    reasons: dict[str, set[str]] = {}

    def add(path: str, reason: str) -> None:
        normalized = canonical_value_path(path) or path
        affected.add(normalized)
        reasons.setdefault(normalized, set()).add(reason)

    for product in products:
        items = list(iter_review_values(product))
        incoming = product.get("source_refs")
        refs = (
            [ref for ref in incoming if isinstance(ref, dict)]
            if isinstance(incoming, list)
            else []
        )
        paths_by_position, mismatched = _canonical_ref_paths(refs, items)
        for path in mismatched:
            add(path, "does not identify a canonical extracted value")
        for position, ref in enumerate(refs):
            forbidden = [
                key
                for key in ("page", "bbox", "source_file_id", "anchor_id", "source_provider")
                if ref.get(key) is not None
            ]
            if forbidden:
                path = paths_by_position.get(position) or str(
                    ref.get("value_path") or "<missing-value-path>"
                )
                add(path, f"contains model-supplied locators: {', '.join(forbidden)}")

        refs_by_path: dict[str, list[dict[str, Any]]] = {}
        for position, path in paths_by_position.items():
            refs_by_path.setdefault(canonical_value_path(path), []).append(
                refs[position]
            )

        for item in items:
            path = canonical_value_path(item.value_path)
            origin = _origin_for(product, item.value_path)
            candidates = refs_by_path.get(path, [])
            if origin in NON_SOURCE_ORIGINS and not is_protected_field(
                item.value_path, item.field
            ):
                continue
            if origin in NON_SOURCE_ORIGINS:
                add(path, "is commerce-sensitive and requires indexed source evidence")
                continue
            if origin in {"merchant_edited", "unknown"}:
                add(path, f"uses invalid new-extraction origin '{origin}'")
                continue
            if not candidates:
                if origin == "source":
                    add(path, "is source-derived but has no evidence_index")
                else:
                    add(
                        path,
                        "needs an evidence_index or explicit derived/defaulted/ai_assisted origin",
                    )
                continue
            valid_indexes = 0
            for ref in candidates:
                evidence_index = _numeric_index(ref.get("evidence_index"))
                if evidence_index is None:
                    add(path, "has a missing or non-numeric evidence_index")
                elif catalog.resolve(evidence_index) is None:
                    add(path, f"has out-of-range evidence_index {evidence_index}")
                else:
                    valid_indexes += 1
            if valid_indexes == 0:
                add(path, "has no valid evidence_index")

    if not affected:
        return EvidenceValidationResult(True, (), "")
    details = "; ".join(
        f"{path}: {', '.join(sorted(reasons[path]))}" for path in sorted(affected)
    )
    feedback = (
        "Source evidence validation failed for these canonical paths: "
        f"{details}. Return the complete products payload. For every source-derived value, "
        "supply value_path plus a numeric evidence_index from the catalog. Otherwise set that "
        "path's value_origins entry to derived, defaulted, or ai_assisted. Do not return coordinates."
    )
    return EvidenceValidationResult(False, tuple(sorted(affected)), feedback)


def blank_unverified_optional_pdf_values(
    products: list[dict[str, Any]], catalog: EvidenceCatalog
) -> tuple[str, ...]:
    """Remove every protected value that has no trusted PDF evidence index."""
    removed_paths: set[str] = set()
    for product in products:
        items = list(iter_review_values(product))
        incoming = product.get("source_refs")
        refs = (
            [ref for ref in incoming if isinstance(ref, dict)]
            if isinstance(incoming, list)
            else []
        )
        paths_by_position, _ = _canonical_ref_paths(refs, items)
        valid_paths = {
            canonical_value_path(path)
            for position, path in paths_by_position.items()
            if (
                (index := _numeric_index(refs[position].get("evidence_index")))
                is not None
                and catalog.resolve(index) is not None
            )
        }
        unsupported_paths = {
            canonical_value_path(item.value_path)
            for item in items
            if is_protected_field(item.value_path, item.field)
            and canonical_value_path(item.value_path) not in valid_paths
        }
        if not unsupported_paths:
            continue
        product_removed_paths: set[str] = set()

        for item in items:
            path = canonical_value_path(item.value_path)
            if path not in unsupported_paths or item.scope != "product":
                continue
            for key in tuple(product):
                if canonical_value_path(key) == canonical_value_path(item.field):
                    product.pop(key, None)
                    product_removed_paths.add(path)

        options = _options_for_variant_subjects(product)
        variants = product.get("variants")
        if isinstance(variants, dict):
            variants = variants.get("nodes")
        for variant in variants if isinstance(variants, list) else []:
            if not isinstance(variant, dict):
                continue
            subject = _variant_subject_for_blanking(variant, options)
            prefix = f"variant[{subject}]"
            inventory = variant.get("inventoryItem") or variant.get("inventory_item")
            for field, aliases in PROTECTED_VARIANT_ALIASES.items():
                candidate_paths = (
                    canonical_value_path(f"{prefix}.{field}"),
                    canonical_value_path(f"inventory[{subject}].{field}"),
                )
                path = next(
                    (candidate for candidate in candidate_paths if candidate in unsupported_paths),
                    None,
                )
                if path is None:
                    continue
                for alias in aliases:
                    variant.pop(alias, None)
                    if isinstance(inventory, dict):
                        inventory.pop(alias, None)
                product_removed_paths.add(path)

        product["source_refs"] = [
            ref
            for position, ref in enumerate(refs)
            if canonical_value_path(paths_by_position.get(position))
            not in product_removed_paths
        ]
        origins = product.get("value_origins")
        if isinstance(origins, dict):
            product["value_origins"] = {
                key: value
                for key, value in origins.items()
                if canonical_value_path(key) not in product_removed_paths
            }
        removed_paths.update(product_removed_paths)
    return tuple(sorted(removed_paths))


def _options_for_variant_subjects(product: dict[str, Any]) -> list[str]:
    raw = (
        product.get("productOptions")
        if isinstance(product.get("productOptions"), list)
        else product.get("options")
    )
    return (
        [
            str(item.get("name") or "").strip()
            for item in raw
            if isinstance(item, dict) and item.get("name")
        ]
        if isinstance(raw, list)
        else []
    )


def _variant_subject_for_blanking(
    variant: dict[str, Any], option_names: list[str]
) -> str:
    pairs: list[str] = []
    for index, name in enumerate(option_names[:3], start=1):
        value = variant.get(f"option{index}")
        if value not in (None, ""):
            pairs.append(f"{name}: {value}")
    subject = (
        " / ".join(pairs)
        or str(
            variant.get("title") or variant.get("id") or variant.get("sku") or "Variant"
        ).strip()
    )
    return re.sub(r"[^a-z0-9]+", "-", subject.casefold()).strip("-") or "value"


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _plain_html(value: str) -> str:
    text = re.sub(r"<\s*br\s*/?\s*>", " ", value, flags=re.IGNORECASE)
    text = re.sub(r"</\s*(?:p|div|li|h[1-6]|tr|td|th)\s*>", " ", text, flags=re.IGNORECASE)
    return _normalized_text(unescape(re.sub(r"<[^>]+>", " ", text)))


def _numeric_representations(value: Any) -> list[str]:
    if isinstance(value, bool):
        return ["true" if value else "false", "yes" if value else "no"]
    if not isinstance(value, (int, float, Decimal)):
        return []
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return []
    plain = format(number, "f")
    trimmed = plain.rstrip("0").rstrip(".") if "." in plain else plain
    variants = [plain, trimmed]
    if number == number.to_integral_value():
        variants.append(format(number, ",.0f"))
    else:
        variants.extend((format(number, ",f").rstrip("0").rstrip("."), f"{number:.2f}"))
    return list(dict.fromkeys(item for item in variants if item))


def _value_representations(value: Any) -> tuple[str, ...]:
    """Return deterministic visible-PDF renderings, most specific first."""
    candidates: list[str] = []
    if isinstance(value, (list, tuple)):
        parts = [_plain_html(str(item)) for item in value if item not in (None, "")]
        for separator in (", ", ",", "; ", " | ", " "):
            if parts:
                candidates.append(separator.join(parts))
    elif isinstance(value, dict):
        # Structured values have no reliable single visible representation.
        return ()
    else:
        candidates.extend(_numeric_representations(value))
        raw = _normalized_text(value)
        if raw:
            candidates.append(raw)
            plain = _plain_html(raw)
            if plain != raw:
                candidates.append(plain)
    return tuple(dict.fromkeys(item for item in candidates if item))


def _rect_overlap(left: fitz.Rect, right: fitz.Rect) -> bool:
    intersection = left & right
    return not intersection.is_empty and intersection.get_area() > 0


def _normalized_bbox(rect: fitz.Rect, page: fitz.Page) -> tuple[float, float, float, float] | None:
    width, height = float(page.rect.width), float(page.rect.height)
    if width <= 0 or height <= 0:
        return None
    values = (
        max(0.0, min(1.0, rect.x0 / width)),
        max(0.0, min(1.0, rect.y0 / height)),
        max(0.0, min(1.0, rect.x1 / width)),
        max(0.0, min(1.0, rect.y1 / height)),
    )
    return values if values[0] < values[2] and values[1] < values[3] else None


def _tight_value_bbox(
    document: fitz.Document | None,
    entry: EvidenceEntry,
    value: Any,
) -> tuple[float, float, float, float] | None:
    if document is None or entry.page < 1 or entry.page > document.page_count:
        return None
    page = document.load_page(entry.page - 1)
    width, height = float(page.rect.width), float(page.rect.height)
    trusted = fitz.Rect(
        entry.bbox[0] * width,
        entry.bbox[1] * height,
        entry.bbox[2] * width,
        entry.bbox[3] * height,
    )
    for representation in _value_representations(value):
        matches = [
            rect
            for rect in page.search_for(representation)
            if _rect_overlap(rect, trusted)
        ]
        # A representation that occurs more than once inside the trusted region
        # is ambiguous. Keep the trusted coarse region instead of guessing.
        if len(matches) > 1:
            return None
        if len(matches) == 1:
            return _normalized_bbox(matches[0] & trusted, page)
    return None


def resolve_indexed_pdf_source_refs(
    products: list[dict[str, Any]],
    catalog: EvidenceCatalog,
    source_file_id: str,
    *,
    pdf_content: bytes | None = None,
    resolution_stats: dict[str, int] | None = None,
) -> int:
    """Replace model locators with trusted coordinates, tightening when unique."""
    resolved_count = 0
    exact_count = 0
    coarse_count = 0
    document: fitz.Document | None = None
    if pdf_content:
        try:
            document = fitz.open(stream=pdf_content, filetype="pdf")
        except Exception:
            document = None
    try:
        for product in products:
            items = list(iter_review_values(product))
            incoming = product.get("source_refs")
            refs = (
                [ref for ref in incoming if isinstance(ref, dict)]
                if isinstance(incoming, list)
                else []
            )
            paths_by_position, _ = _canonical_ref_paths(refs, items)
            item_by_path = {canonical_value_path(item.value_path): item for item in items}
            verified: list[dict[str, Any]] = []
            origins = (
                product.get("value_origins")
                if isinstance(product.get("value_origins"), dict)
                else {}
            )
            canonical_origins = {
                canonical_value_path(key): value for key, value in origins.items()
            }
            for position, canonical_path in paths_by_position.items():
                item = item_by_path.get(canonical_value_path(canonical_path))
                if (
                    item is None
                    or _origin_for(product, item.value_path) in NON_SOURCE_ORIGINS
                ):
                    continue
                evidence_index = _numeric_index(refs[position].get("evidence_index"))
                entry = (
                    catalog.resolve(evidence_index) if evidence_index is not None else None
                )
                if entry is None:
                    continue
                tight_bbox = _tight_value_bbox(document, entry, item.value)
                bbox = tight_bbox or entry.bbox
                if tight_bbox is None:
                    coarse_count += 1
                else:
                    exact_count += 1
                verified.append(
                    {
                        "source_file_id": source_file_id,
                        "field": item.field,
                        "value_path": item.value_path,
                        "document_kind": "pdf",
                        "anchor_id": entry.anchor_id,
                        "page": entry.page,
                        "bbox": list(bbox),
                        "value": str(item.value),
                        "source_provider": entry.provider,
                    }
                )
                canonical_origins[canonical_value_path(item.value_path)] = "source"
                resolved_count += 1
            product["source_refs"] = verified
            if canonical_origins:
                product["value_origins"] = canonical_origins
    finally:
        if document is not None:
            document.close()
    if resolution_stats is not None:
        resolution_stats.update(exact=exact_count, coarse=coarse_count)
    return resolved_count
