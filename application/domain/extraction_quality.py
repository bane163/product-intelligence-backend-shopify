"""Deterministic post-extraction quality checks for merchant imports."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any


QUALITY_REPORT_VERSION = 1
QUALITY_STATUS_RUNNING = "running"
QUALITY_STATUS_READY = "ready"
QUALITY_STATUS_LIVE_PENDING = "live_validation_pending"

_HANDLE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VALID_STATUSES = {"ACTIVE", "ARCHIVED", "DRAFT", "UNLISTED"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _variants(product: dict[str, Any]) -> list[dict[str, Any]]:
    raw = product.get("variants")
    if isinstance(raw, dict):
        raw = raw.get("nodes")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _price(variant: dict[str, Any]) -> Any:
    return variant.get("price")


def _issue(
    *,
    severity: str,
    code: str,
    product_index: int | None,
    value_path: str | None,
    message: str,
    remedy: str,
) -> dict[str, Any]:
    suffix = "import" if product_index is None else f"product-{product_index}"
    if value_path:
        suffix += f"-{value_path}"
    return {
        "issue_id": f"{code.lower()}:{suffix}",
        "severity": severity,
        "code": code,
        "product_index": product_index,
        "value_path": value_path,
        "message": message,
        "remedy": remedy,
    }


def _has_value_evidence(product: dict[str, Any], value_path: str) -> bool:
    refs = product.get("source_refs")
    if not isinstance(refs, list):
        return False
    return any(
        isinstance(ref, dict)
        and str(ref.get("value_path") or ref.get("field") or "") == value_path
        and bool(ref.get("cell") or ref.get("cell_range") or ref.get("anchor_id"))
        for ref in refs
    )


def build_quality_report(
    products: list[dict[str, Any]], *, status: str = QUALITY_STATUS_READY
) -> dict[str, Any]:
    """Return a stable merchant-facing report without mutating products."""
    issues: list[dict[str, Any]] = []
    sku_owners: dict[str, tuple[int, int]] = {}
    handle_owners: dict[str, int] = {}

    if not products:
        issues.append(_issue(
            severity="blocker", code="NO_PRODUCTS_EXTRACTED", product_index=None,
            value_path=None, message="No products could be extracted from this document.",
            remedy="retry_extraction",
        ))

    for product_index, product in enumerate(products):
        title = _text(product.get("title"))
        if not title:
            issues.append(_issue(
                severity="blocker", code="TITLE_REQUIRED", product_index=product_index,
                value_path="title", message="Enter a product title.", remedy="edit",
            ))
        elif not _has_value_evidence(product, "title"):
            issues.append(_issue(
                severity="warning", code="SOURCE_EVIDENCE_MISSING", product_index=product_index,
                value_path="title", message="The product title could not be linked to one exact source location.",
                remedy="view_source",
            ))

        handle = _text(product.get("handle")).lower()
        if handle:
            if not _HANDLE_RE.fullmatch(handle):
                issues.append(_issue(
                    severity="blocker", code="INVALID_HANDLE", product_index=product_index,
                    value_path="handle", message="Use lowercase letters, numbers, and single hyphens only.",
                    remedy="edit",
                ))
            elif handle in handle_owners:
                issues.append(_issue(
                    severity="blocker", code="DUPLICATE_IMPORT_HANDLE", product_index=product_index,
                    value_path="handle", message="Each product in this import must have a unique handle.",
                    remedy="edit",
                ))
            else:
                handle_owners[handle] = product_index

        status_value = _text(product.get("status"))
        if status_value and status_value.upper() not in _VALID_STATUSES:
            issues.append(_issue(
                severity="blocker", code="INVALID_STATUS", product_index=product_index,
                value_path="status", message="Choose Active, Draft, Archived, or Unlisted.", remedy="edit",
            ))

        if not _text(product.get("vendor")):
            issues.append(_issue(
                severity="warning", code="VENDOR_MISSING", product_index=product_index,
                value_path="vendor", message="No vendor was extracted for this product.", remedy="edit",
            ))
        images = product.get("images")
        if not _text(product.get("image_src")) and not (
            isinstance(images, list) and any(isinstance(item, dict) and _text(item.get("src")) for item in images)
        ):
            issues.append(_issue(
                severity="warning", code="IMAGE_MISSING", product_index=product_index,
                value_path="images", message="No product image was extracted.", remedy="edit",
            ))

        variants = _variants(product)
        if not variants:
            issues.append(_issue(
                severity="blocker", code="SELLABLE_VARIANT_REQUIRED", product_index=product_index,
                value_path="variants", message="Add at least one variant with a SKU and price.", remedy="edit",
            ))
        for variant_index, variant in enumerate(variants):
            prefix = f"variants[{variant_index}]"
            sku = _text(variant.get("sku"))
            if not sku:
                issues.append(_issue(
                    severity="blocker", code="SKU_REQUIRED", product_index=product_index,
                    value_path=f"{prefix}.sku", message="Enter a SKU for every variant.", remedy="edit",
                ))
            else:
                folded_sku = sku.casefold()
                if folded_sku in sku_owners:
                    issues.append(_issue(
                        severity="blocker", code="DUPLICATE_IMPORT_SKU", product_index=product_index,
                        value_path=f"{prefix}.sku", message="Each variant in this import must have a unique SKU.",
                        remedy="edit",
                    ))
                else:
                    sku_owners[folded_sku] = (product_index, variant_index)

            raw_price = _price(variant)
            try:
                price = float(raw_price)
            except (TypeError, ValueError):
                price = math.nan
            if raw_price in (None, "") or not math.isfinite(price) or price < 0:
                issues.append(_issue(
                    severity="blocker", code="PRICE_REQUIRED", product_index=product_index,
                    value_path=f"{prefix}.price", message="Enter a finite price that is zero or greater.", remedy="edit",
                ))
            compare = variant.get("compare_at_price", variant.get("compareAtPrice"))
            if compare not in (None, ""):
                try:
                    compare_value = float(compare)
                except (TypeError, ValueError):
                    compare_value = math.nan
                if not math.isfinite(compare_value) or (math.isfinite(price) and compare_value < price):
                    issues.append(_issue(
                        severity="blocker", code="INVALID_COMPARE_AT_PRICE", product_index=product_index,
                        value_path=f"{prefix}.compare_at_price",
                        message="Compare-at price must be finite and at least the selling price.", remedy="edit",
                    ))

        deferred = product.get("inventory_quantity")
        if deferred not in (None, ""):
            issues.append(_issue(
                severity="warning", code="FIELD_DEFERRED", product_index=product_index,
                value_path="inventory_quantity",
                message="Inventory quantities require a Shopify fulfillment location and will not be applied yet.",
                remedy="review",
            ))

    blocker_products = {
        int(item["product_index"]) for item in issues
        if item["severity"] == "blocker" and isinstance(item.get("product_index"), int)
    }
    blocker_count = sum(item["severity"] == "blocker" for item in issues)
    warning_count = sum(item["severity"] == "warning" for item in issues)
    return {
        "version": QUALITY_REPORT_VERSION,
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "product_count": len(products),
            "valid_product_count": max(0, len(products) - len(blocker_products)),
            "blocked_product_count": len(blocker_products),
            "blocker_count": blocker_count,
            "warning_count": warning_count,
        },
        "issues": issues,
    }


def issues_for_product(report: dict[str, Any] | None, product_index: int) -> list[dict[str, Any]]:
    if not isinstance(report, dict) or not isinstance(report.get("issues"), list):
        return []
    return [
        item for item in report["issues"]
        if isinstance(item, dict) and item.get("product_index") == product_index
    ]
