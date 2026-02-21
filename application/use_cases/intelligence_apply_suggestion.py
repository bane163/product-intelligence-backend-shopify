from __future__ import annotations

import uuid
from typing import Any

from application.domain.product import extract_first_sku
from application.ports.shopify_port import ShopifyPort
from application.ports.supabase_port import SupabasePort

REVERT_MODES_KEY = "__revert_modes"
REVERT_REVERSIBLE_KEY = "__is_reversible"
REVERT_REASON_KEY = "__non_reversible_reason"
REVERT_MODE_RESTORE = "restore"
REVERT_MODE_CLEAR = "clear"
REVERT_MODE_UNSUPPORTED_CLEAR = "unsupported_clear"
_CLEARABLE_UNSET_FIELDS = {
    "vendor",
    "product_type",
    "product_category",
    "body_html",
    "seo_title",
    "seo_description",
    "tags",
}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _extract_audited_products(audit: dict[str, Any]) -> list[dict[str, Any]]:
    totals = audit.get("totals")
    if not isinstance(totals, dict):
        return []
    raw = totals.get("audited_products")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _resolve_suggestion_product(
    suggestion: dict[str, Any], audit: dict[str, Any]
) -> dict[str, Any]:
    products = _extract_audited_products(audit)
    if not products:
        raise ValueError("Audit does not include audited product metadata")

    try:
        product_index = int(suggestion.get("product_index") or 0)
    except (TypeError, ValueError):
        product_index = 0
    if 0 <= product_index < len(products):
        return dict(products[product_index])

    target_title = _normalize_text(suggestion.get("product_title"))
    if target_title:
        for item in products:
            if _normalize_text(item.get("title")) == target_title:
                return dict(item)

    raise ValueError("Unable to resolve suggestion target product from audit metadata")


async def _resolve_product_gid(shopify: ShopifyPort, product: dict[str, Any]) -> str | None:
    gid = product.get("id") or product.get("shopify_gid")
    if isinstance(gid, str) and gid:
        return gid

    handle = product.get("handle")
    if isinstance(handle, str) and handle:
        resolved = await shopify.find_product_id_by_handle(handle)
        if resolved:
            return resolved

    sku = extract_first_sku(product)
    if sku:
        resolved = await shopify.find_product_id_by_sku(sku)
        if resolved:
            return resolved

    return None


def _normalize_revert_field(field: str) -> str:
    return "product_type" if field == "product_category" else field


def _is_unset_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, list):
        return len(value) == 0
    return False


def _supports_field_clear(field: str) -> bool:
    return _normalize_revert_field(field) in _CLEARABLE_UNSET_FIELDS


def _normalize_shopify_product(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    node = data.get("node") if isinstance(data, dict) else None
    if not isinstance(node, dict):
        return {}
    seo = node.get("seo") if isinstance(node.get("seo"), dict) else {}
    return {
        "id": node.get("id"),
        "title": node.get("title"),
        "body_html": node.get("descriptionHtml"),
        "vendor": node.get("vendor"),
        "handle": node.get("handle"),
        "product_type": node.get("productType"),
        "status": node.get("status"),
        "tags": node.get("tags"),
        "seo_title": seo.get("title"),
        "seo_description": seo.get("description"),
    }

def _normalize_metafields_payload(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        namespace = str(item.get("namespace") or "").strip()
        key = str(item.get("key") or "").strip()
        value = item.get("value")
        if not namespace or not key or value is None:
            continue
        normalized_item: dict[str, Any] = {
            "namespace": namespace,
            "key": key,
            "value": str(value),
        }
        value_type = item.get("type")
        if isinstance(value_type, str) and value_type.strip():
            normalized_item["type"] = value_type.strip()
        normalized.append(normalized_item)
    return normalized


def _normalize_variant_operations(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    create_options_raw = raw.get("create_options")
    create_options: list[dict[str, Any]] = []
    if isinstance(create_options_raw, list):
        for option in create_options_raw:
            if not isinstance(option, dict):
                continue
            name = str(option.get("name") or "").strip()
            values_raw = option.get("values")
            values = [str(item).strip() for item in values_raw if str(item).strip()] if isinstance(values_raw, list) else []
            if name and values:
                create_options.append({"name": name, "values": values})

    create_variants_raw = raw.get("create_variants")
    create_variants: list[dict[str, Any]] = []
    if isinstance(create_variants_raw, list):
        for variant in create_variants_raw:
            if not isinstance(variant, dict):
                continue
            option_values_raw = variant.get("option_values")
            option_values = [item for item in option_values_raw if isinstance(item, dict)] if isinstance(option_values_raw, list) else []
            if not option_values:
                continue
            payload: dict[str, Any] = {"option_values": option_values}
            sku = str(variant.get("sku") or "").strip()
            if sku:
                payload["sku"] = sku
            price = variant.get("price")
            if price not in (None, ""):
                payload["price"] = str(price)
            inventory_quantity = variant.get("inventory_quantity")
            if isinstance(inventory_quantity, int):
                payload["inventory_quantity"] = inventory_quantity
            create_variants.append(payload)

    defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    return {
        "create_options": create_options,
        "create_variants": create_variants,
        "defaults": defaults,
    }


def _extract_user_errors(response: dict[str, Any], path: list[str]) -> list[str]:
    cursor: Any = response
    for key in path:
        if not isinstance(cursor, dict):
            return []
        cursor = cursor.get(key)
    if not isinstance(cursor, list):
        return []
    messages: list[str] = []
    for item in cursor:
        if isinstance(item, dict):
            message = str(item.get("message") or "").strip()
            if message:
                messages.append(message)
    return messages


async def _apply_variant_operations(
    *,
    shopify: ShopifyPort,
    product_id: str,
    variant_operations: dict[str, Any],
) -> dict[str, Any]:
    normalized = _normalize_variant_operations(variant_operations)
    created_option_names: list[str] = []
    created_variant_ids: list[str] = []

    create_options = normalized.get("create_options") if isinstance(normalized.get("create_options"), list) else []
    if create_options:
        options_resp = await shopify.create_product_options(product_id, create_options)
        errors = _extract_user_errors(options_resp, ["data", "productOptionsCreate", "userErrors"])
        if errors:
            raise ValueError(f"Failed creating product options: {', '.join(errors)}")
        created_option_names = [str(item.get("name") or "").strip() for item in create_options if isinstance(item, dict) and str(item.get("name") or "").strip()]

    create_variants = normalized.get("create_variants") if isinstance(normalized.get("create_variants"), list) else []
    if create_variants:
        variants_resp = await shopify.bulk_create_product_variants(product_id, create_variants)
        errors = _extract_user_errors(variants_resp, ["data", "productVariantsBulkCreate", "userErrors"])
        if errors:
            raise ValueError(f"Failed creating product variants: {', '.join(errors)}")
        created_variants = variants_resp.get("data", {}).get("productVariantsBulkCreate", {}).get("productVariants", []) if isinstance(variants_resp, dict) else []
        if isinstance(created_variants, list):
            for item in created_variants:
                if not isinstance(item, dict):
                    continue
                variant_id = str(item.get("id") or "").strip()
                if variant_id:
                    created_variant_ids.append(variant_id)

    return {
        "created_option_names": created_option_names,
        "created_variant_ids": created_variant_ids,
    }


def _build_previous_payload(
    *,
    patch_payload: dict[str, Any],
    live_product: dict[str, Any],
    fallback_product: dict[str, Any],
    existing_metafields: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    previous_payload: dict[str, Any] = {REVERT_MODES_KEY: {}}
    unsupported_fields: list[str] = []
    revert_modes = previous_payload[REVERT_MODES_KEY]
    assert isinstance(revert_modes, dict)

    for key in patch_payload.keys():
        if key == "variant_operations":
            previous_payload[key] = {}
            revert_modes[key] = REVERT_MODE_RESTORE
            continue
        if key == "metafields":
            previous_metafields: list[dict[str, Any]] = []
            requested_metafields = _normalize_metafields_payload(patch_payload.get(key))
            missing_metafields: list[str] = []
            for requested in requested_metafields:
                identity = (requested["namespace"], requested["key"])
                existing = (existing_metafields or {}).get(identity)
                existing_value = existing.get("value") if isinstance(existing, dict) else None
                if _is_unset_value(existing_value):
                    missing_metafields.append(f"{requested['namespace']}.{requested['key']}")
                    continue
                restore_item: dict[str, Any] = {
                    "namespace": requested["namespace"],
                    "key": requested["key"],
                    "value": str(existing_value),
                }
                existing_type = existing.get("type") if isinstance(existing, dict) else None
                if isinstance(existing_type, str) and existing_type.strip():
                    restore_item["type"] = existing_type.strip()
                previous_metafields.append(restore_item)

            previous_payload[key] = previous_metafields
            if missing_metafields:
                revert_modes[key] = REVERT_MODE_UNSUPPORTED_CLEAR
                unsupported_fields.extend(missing_metafields)
            else:
                revert_modes[key] = REVERT_MODE_RESTORE
            continue

        source_key = _normalize_revert_field(key)
        source_value = (
            live_product[source_key]
            if source_key in live_product
            else fallback_product.get(source_key)
        )
        previous_payload[key] = source_value
        if _is_unset_value(source_value):
            if _supports_field_clear(key):
                revert_modes[key] = REVERT_MODE_CLEAR
            else:
                revert_modes[key] = REVERT_MODE_UNSUPPORTED_CLEAR
                unsupported_fields.append(key)
        else:
            revert_modes[key] = REVERT_MODE_RESTORE

    previous_payload[REVERT_REVERSIBLE_KEY] = len(unsupported_fields) == 0
    if unsupported_fields:
        previous_payload[REVERT_REASON_KEY] = (
            "Auto-revert is unavailable for fields with no original value: "
            + ", ".join(sorted(unsupported_fields))
        )
    return previous_payload


def _build_effective_patch_payload(
    *,
    saved_patch_payload: dict[str, Any],
    requested_patch_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(requested_patch_payload, dict):
        return dict(saved_patch_payload)
    effective: dict[str, Any] = {}
    for key in saved_patch_payload.keys():
        if key in requested_patch_payload:
            effective[key] = requested_patch_payload[key]
    return effective


def _build_remaining_patch_payload(
    *,
    saved_patch_payload: dict[str, Any],
    applied_patch_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: value
        for key, value in saved_patch_payload.items()
        if key not in applied_patch_payload
    }


def _build_pending_suggestion_from_partial_apply(
    *,
    source_suggestion: dict[str, Any],
    remaining_patch_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "suggestion_id": str(uuid.uuid4()),
        "audit_id": source_suggestion.get("audit_id"),
        "finding_id": source_suggestion.get("finding_id"),
        "product_index": source_suggestion.get("product_index"),
        "product_title": source_suggestion.get("product_title"),
        "category": source_suggestion.get("category"),
        "severity": source_suggestion.get("severity"),
        "message": source_suggestion.get("message"),
        "patch_payload": remaining_patch_payload,
        "status": "pending",
        "applied_at": None,
    }


async def execute(
    *,
    supabase: SupabasePort,
    shopify: ShopifyPort,
    suggestion_id: str,
    patch_payload: dict[str, Any] | None = None,
    shop_domain: str | None = None,
) -> dict[str, Any]:
    suggestion = supabase.get_product_intelligence_suggestion(
        suggestion_id,
        shop_domain=shop_domain,
    )
    if not suggestion:
        raise LookupError("Suggestion not found")

    if str(suggestion.get("status") or "") == "applied":
        return {"status": "applied", "suggestion": suggestion, "shopify_updated": False}

    saved_patch_payload = suggestion.get("patch_payload")
    if not isinstance(saved_patch_payload, dict) or not saved_patch_payload:
        raise ValueError("Suggestion has no actionable patch payload")
    effective_patch_payload = _build_effective_patch_payload(
        saved_patch_payload=saved_patch_payload,
        requested_patch_payload=patch_payload,
    )
    if not effective_patch_payload:
        raise ValueError("Suggestion has no actionable patch payload")
    remaining_patch_payload = _build_remaining_patch_payload(
        saved_patch_payload=saved_patch_payload,
        applied_patch_payload=effective_patch_payload,
    )
    variant_operations = _normalize_variant_operations(effective_patch_payload.get("variant_operations"))

    audit_id = suggestion.get("audit_id")
    if not isinstance(audit_id, str) or not audit_id:
        raise ValueError("Suggestion is missing audit reference")

    audit = supabase.get_product_intelligence_audit(audit_id, shop_domain=shop_domain)
    if not audit:
        raise LookupError("Audit not found for suggestion")

    current_product = _resolve_suggestion_product(suggestion=suggestion, audit=audit)
    gid = await _resolve_product_gid(shopify, current_product)
    if not gid:
        raise ValueError("Unable to resolve Shopify product ID for suggestion target")
    live_product = _normalize_shopify_product(await shopify.get_product(gid))
    requested_metafields = _normalize_metafields_payload(
        effective_patch_payload.get("metafields")
    )
    existing_metafield_map: dict[tuple[str, str], dict[str, Any]] = {}
    if requested_metafields:
        metafield_identifiers = [
            {"namespace": item["namespace"], "key": item["key"]}
            for item in requested_metafields
        ]
        existing_metafields = await shopify.get_product_metafields(
            gid, metafield_identifiers
        )
        for item in existing_metafields:
            if not isinstance(item, dict):
                continue
            namespace = str(item.get("namespace") or "").strip()
            key = str(item.get("key") or "").strip()
            if not namespace or not key:
                continue
            existing_metafield_map[(namespace, key)] = item
    previous_payload = _build_previous_payload(
        patch_payload=effective_patch_payload,
        live_product=live_product,
        fallback_product=current_product,
        existing_metafields=existing_metafield_map,
    )
    variant_operation_result = {}
    if variant_operations.get("create_options") or variant_operations.get("create_variants"):
        variant_operation_result = await _apply_variant_operations(
            shopify=shopify,
            product_id=gid,
            variant_operations=variant_operations,
        )
        previous_payload["variant_operations"] = variant_operation_result

    shopify_patch_payload = {
        key: value for key, value in effective_patch_payload.items() if key != "variant_operations"
    }
    if shopify_patch_payload:
        await shopify.update_product_from_input({"id": gid, **shopify_patch_payload})

    applied = supabase.mark_product_intelligence_suggestion_applied(
        suggestion_id=suggestion_id,
        previous_payload=previous_payload,
        patch_payload=effective_patch_payload,
        shop_domain=shop_domain,
    )
    if not applied:
        raise RuntimeError("Failed to mark suggestion as applied")
    if remaining_patch_payload:
        pending_copy = _build_pending_suggestion_from_partial_apply(
            source_suggestion=suggestion,
            remaining_patch_payload=remaining_patch_payload,
        )
        created_pending = supabase.create_product_intelligence_suggestion(
            suggestion=pending_copy,
            shop_domain=shop_domain,
        )
        if not created_pending:
            raise RuntimeError("Failed to keep remaining suggestion fields pending")

    return {
        "status": "applied",
        "suggestion": applied,
        "shopify_updated": True,
        "target_product_id": gid,
    }
