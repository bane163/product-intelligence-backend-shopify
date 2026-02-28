from __future__ import annotations

import json
import re
import uuid
from typing import Any

from pydantic import ValidationError

from ai.agent_client import run_product_intelligence_suggestions
from ai.models import ProductIntelligenceSuggestionsList
from application.ports.supabase_port import SupabaseNamespacedPort

_SIZE_ALIAS_MAP: dict[str, str] = {
    "xs": "XS",
    "extra small": "XS",
    "small": "S",
    "sm": "S",
    "s": "S",
    "medium": "M",
    "med": "M",
    "m": "M",
    "large": "L",
    "lg": "L",
    "l": "L",
    "extra large": "XL",
    "x-large": "XL",
    "x large": "XL",
    "xl": "XL",
    "xxl": "XXL",
    "2xl": "XXL",
    "one size": "ONE_SIZE",
    "onesize": "ONE_SIZE",
}

_SUPPLIER_SIZE_MAP: dict[str, str] = {
    "x-large": "XL",
    "46": "XL",
    "1x": "1X",
    "xl": "XL",
}

_CHILDREN_SIZE_MAP: dict[str, str] = {
    "2t": "2T",
    "24m": "2T",
    "2 years": "2T",
    "2": "2T",
}

_SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "1X", "2X", "3X"]

_COLOR_TOKENS = (
    "black", "white", "blue", "navy", "red", "green", "yellow", "orange",
    "purple", "pink", "brown", "beige", "gray", "grey",
)

_MATERIAL_TOKENS = (
    "cotton", "polyester", "wool", "linen", "leather", "denim", "silk", "nylon",
)

_NORMALIZATION_CATEGORIES = {
    "size_alias": "normalization_size_alias",
    "mixed_units": "normalization_mixed_units",
    "structured_unstructured_size": "normalization_structured_unstructured_size",
    "dimensions_format": "normalization_dimensions_format",
    "supplier_size": "normalization_supplier_size",
    "variant_ordering": "normalization_variant_ordering",
    "description_extraction": "normalization_description_extraction",
    "children_size": "normalization_children_size",
    "missing_options": "normalization_missing_options",
}


def _default_normalization_settings() -> dict[str, Any]:
    return {
        "unit_system": "metric",
        "confidence_threshold": None,
        "categories": {
            "size_alias": True,
            "mixed_units": True,
            "structured_unstructured_size": True,
            "dimensions_format": True,
            "supplier_size": True,
            "variant_ordering": True,
            "description_extraction": True,
            "children_size": True,
            "missing_options": True,
        },
    }


def _strip_markdown_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return stripped


def _resolve_model_env(supabase: SupabaseNamespacedPort, shop_domain: str) -> dict[str, str]:
    active_model = supabase.llm_configs.get_active_llm_model_config(shop_domain)
    if not active_model:
        raise ValueError("No active LLM model config found for this shop")
    base_url = str(active_model.get("base_url") or "").strip()
    model_id = str(active_model.get("model_id") or "").strip()
    api_key = str(active_model.get("api_key") or "").strip()
    if not base_url or not model_id or not api_key:
        raise ValueError("Active LLM model config is incomplete")
    return {
        "OLLAMA_CLOUD_URL": base_url,
        "OLLAMA_MODEL_ID": model_id,
        "OLLAMA_API_KEY": api_key,
    }


def _parse_suggestion_payload(raw_response: Any) -> ProductIntelligenceSuggestionsList:
    if isinstance(raw_response, ProductIntelligenceSuggestionsList):
        return raw_response
    if raw_response is None:
        raise ValueError("LLM response payload is empty")
    try:
        return ProductIntelligenceSuggestionsList.model_validate(raw_response)
    except ValidationError as exc:
        raise ValueError(f"Malformed LLM suggestion payload: {exc}") from exc


def _parse_suggestion_text(raw_text: str) -> ProductIntelligenceSuggestionsList:
    normalized = _strip_markdown_json_fence(raw_text)
    if not normalized:
        raise ValueError("LLM response text is empty")
    try:
        return ProductIntelligenceSuggestionsList.model_validate_json(normalized)
    except ValidationError as exc:
        raise ValueError(f"Malformed LLM suggestion response: {exc}") from exc


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value)


def _collect_variant_size_tokens(product: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    variants = product.get("variants") if isinstance(product.get("variants"), list) else []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        for key in ("option1", "option2", "option3", "title", "size"):
            value = _to_text(variant.get(key))
            if value:
                tokens.append(value)
        selected_options = variant.get("selectedOptions")
        if isinstance(selected_options, list):
            for option in selected_options:
                if not isinstance(option, dict):
                    continue
                option_value = _to_text(option.get("value"))
                if option_value:
                    tokens.append(option_value)
    return tokens


def _normalize_size_token(token: str) -> str:
    normalized = token.strip().lower()
    normalized = re.sub(r"\(.*?\)", "", normalized).strip()
    normalized = normalized.replace("—", "-")
    return normalized


def _extract_existing_option_names(product: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    options = product.get("options") if isinstance(product.get("options"), list) else []
    for option in options:
        if not isinstance(option, dict):
            continue
        name = _to_text(option.get("name")).lower()
        if name:
            names.add(name)

    variants = product.get("variants") if isinstance(product.get("variants"), list) else []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        selected_options = variant.get("selectedOptions")
        if isinstance(selected_options, list):
            for selected in selected_options:
                if not isinstance(selected, dict):
                    continue
                name = _to_text(selected.get("name")).lower()
                if name:
                    names.add(name)
    return names


def _extract_inferred_dimensions(
    *,
    title: str,
    body: str,
    existing_option_names: set[str],
) -> list[dict[str, Any]]:
    title_lower = title.lower()
    body_lower = body.lower()
    merged_text = f"{title_lower} {body_lower}".strip()
    dimensions: list[dict[str, Any]] = []

    size_hits: list[tuple[str, str, str]] = []
    for raw, canonical in _SIZE_ALIAS_MAP.items():
        if not raw or len(raw) < 2:
            continue
        if re.search(rf"\b{re.escape(raw)}\b", merged_text):
            source = "title" if re.search(rf"\b{re.escape(raw)}\b", title_lower) else "description"
            size_hits.append((raw, canonical, source))
    if size_hits and "size" not in existing_option_names:
        detected = [item[0] for item in size_hits]
        canonical_values = sorted({item[1] for item in size_hits}, key=lambda item: _SIZE_ORDER.index(item) if item in _SIZE_ORDER else 999)
        evidence_sources = sorted({item[2] for item in size_hits})
        dimensions.append(
            {
                "dimension": "Size",
                "detected_values": detected,
                "canonical_values": canonical_values,
                "evidence_sources": evidence_sources,
                "confidence": 0.9,
            }
        )

    color_hits = [token for token in _COLOR_TOKENS if re.search(rf"\b{re.escape(token)}\b", merged_text)]
    if color_hits and "color" not in existing_option_names:
        dimensions.append(
            {
                "dimension": "Color",
                "detected_values": color_hits,
                "canonical_values": [item.title() for item in sorted(set(color_hits))],
                "evidence_sources": ["title" if any(re.search(rf"\b{re.escape(t)}\b", title_lower) for t in color_hits) else "description"],
                "confidence": 0.84,
            }
        )

    material_hits = [token for token in _MATERIAL_TOKENS if re.search(rf"\b{re.escape(token)}\b", merged_text)]
    if material_hits and "material" not in existing_option_names:
        dimensions.append(
            {
                "dimension": "Material",
                "detected_values": material_hits,
                "canonical_values": [item.title() for item in sorted(set(material_hits))],
                "evidence_sources": ["title" if any(re.search(rf"\b{re.escape(t)}\b", title_lower) for t in material_hits) else "description"],
                "confidence": 0.82,
            }
        )

    return dimensions


def _derive_sku_prefix(product: dict[str, Any], title: str) -> str:
    variants = product.get("variants") if isinstance(product.get("variants"), list) else []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        sku = _to_text(variant.get("sku"))
        if not sku:
            continue
        for sep in ("-", "_", "/"):
            if sep in sku:
                prefix = sku.rsplit(sep, 1)[0].strip()
                if prefix:
                    return prefix
        return sku
    normalized_title = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").upper()
    return normalized_title[:24] if normalized_title else "SKU"


def _sanitize_sku_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").upper()
    return token or "OPT"


def _build_variant_operations(
    *,
    product: dict[str, Any],
    title: str,
    dimensions: list[dict[str, Any]],
) -> dict[str, Any]:
    create_options = [
        {
            "name": item["dimension"],
            "values": item["canonical_values"],
        }
        for item in dimensions
        if item.get("canonical_values")
    ]

    variants = product.get("variants") if isinstance(product.get("variants"), list) else []
    baseline = variants[0] if variants and isinstance(variants[0], dict) else {}
    primary = dimensions[0] if dimensions else {"dimension": "Size", "canonical_values": []}
    option_name = str(primary.get("dimension") or "Size")
    sku_prefix = _derive_sku_prefix(product, title)

    create_variants: list[dict[str, Any]] = []
    for raw_value in primary.get("canonical_values") or []:
        value = str(raw_value).strip()
        if not value:
            continue
        variant_payload: dict[str, Any] = {
            "option_values": [{"option_name": option_name, "name": value}],
            "sku": f"{sku_prefix}-{_sanitize_sku_token(value)}",
        }
        price = baseline.get("price") if isinstance(baseline, dict) else None
        if price not in (None, ""):
            variant_payload["price"] = str(price)
        inventory_quantity = baseline.get("inventory_quantity") if isinstance(baseline, dict) else None
        if isinstance(inventory_quantity, int):
            variant_payload["inventory_quantity"] = inventory_quantity
        create_variants.append(variant_payload)

    return {
        "create_options": create_options,
        "create_variants": create_variants,
        "defaults": {
            "copy_from_first_variant": True,
            "requires_review": True,
        },
    }


def _metafield(namespace: str, key: str, value: Any, mf_type: str = "single_line_text_field") -> dict[str, str]:
    return {
        "namespace": namespace,
        "key": key,
        "value": str(value),
        "type": mf_type,
    }


def _create_normalization_suggestion(
    *,
    product_index: int,
    product_title: str,
    category_key: str,
    message: str,
    severity: str,
    confidence: float,
    metafields: list[dict[str, str]] | None,
    details: dict[str, Any],
    shop_domain: str,
    patch_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = patch_payload if isinstance(patch_payload, dict) and patch_payload else {"metafields": metafields or []}
    return {
        "suggestion_id": str(uuid.uuid4()),
        "finding_id": str(uuid.uuid4()),
        "product_index": product_index,
        "product_title": product_title,
        "category": _NORMALIZATION_CATEGORIES[category_key],
        "severity": severity,
        "message": message,
        "patch_payload": payload,
        "details": {**details, "confidence": confidence, "rule_id": category_key},
        "status": "pending",
        "shop_domain": shop_domain,
    }


def _build_normalization_suggestions(
    *,
    products: list[dict[str, Any]],
    shop_domain: str,
    normalization_settings: dict[str, Any],
) -> list[dict[str, Any]]:
    settings = {
        **_default_normalization_settings(),
        **(normalization_settings or {}),
    }
    categories = settings.get("categories") if isinstance(settings.get("categories"), dict) else {}
    threshold_raw = settings.get("confidence_threshold")
    confidence_threshold = (
        max(0.0, min(1.0, float(threshold_raw)))
        if isinstance(threshold_raw, (int, float))
        else None
    )
    unit_system = str(settings.get("unit_system") or "metric").strip().lower()
    if unit_system not in {"metric", "imperial"}:
        unit_system = "metric"

    suggestions: list[dict[str, Any]] = []

    def allowed(category_key: str, confidence: float) -> bool:
        if not bool(categories.get(category_key, True)):
            return False
        if confidence_threshold is None:
            return True
        return confidence >= confidence_threshold

    for index, product in enumerate(products):
        title = _to_text(product.get("title")) or f"Product #{index + 1}"
        body = _strip_html(_to_text(product.get("body_html") or product.get("descriptionHtml")))
        body_lower = body.lower()
        size_tokens = _collect_variant_size_tokens(product)
        existing_option_names = _extract_existing_option_names(product)

        canonical_sizes: list[str] = []
        alias_pairs: list[tuple[str, str]] = []
        supplier_pairs: list[tuple[str, str]] = []
        children_pairs: list[tuple[str, str]] = []
        has_numeric_size = False
        has_text_size = False

        for raw_token in size_tokens:
            token = _normalize_size_token(raw_token)
            if not token:
                continue
            if re.fullmatch(r"\d+(\.\d+)?", token):
                has_numeric_size = True
            if re.search(r"[a-z]", token):
                has_text_size = True

            canonical = _SIZE_ALIAS_MAP.get(token)
            if canonical:
                canonical_sizes.append(canonical)
                if canonical != raw_token.strip().upper():
                    alias_pairs.append((raw_token, canonical))

            supplier = _SUPPLIER_SIZE_MAP.get(token)
            if supplier:
                supplier_pairs.append((raw_token, supplier))
                canonical_sizes.append(supplier)

            child = _CHILDREN_SIZE_MAP.get(token)
            if child:
                children_pairs.append((raw_token, child))
                canonical_sizes.append(child)

        if alias_pairs and allowed("size_alias", 0.96):
            suggestions.append(
                _create_normalization_suggestion(
                    product_index=index,
                    product_title=title,
                    category_key="size_alias",
                    message="Variant size naming is inconsistent; normalize aliases to a canonical size label.",
                    severity="medium",
                    confidence=0.96,
                    metafields=[
                        _metafield(
                            "extractor",
                            "normalized_size_alias_map",
                            json.dumps({raw: canonical for raw, canonical in alias_pairs}),
                            "json",
                        ),
                    ],
                    details={
                        "detected_value": [raw for raw, _ in alias_pairs],
                        "canonical_value": [canonical for _, canonical in alias_pairs],
                    },
                    shop_domain=shop_domain,
                )
            )

        if supplier_pairs and allowed("supplier_size", 0.93):
            suggestions.append(
                _create_normalization_suggestion(
                    product_index=index,
                    product_title=title,
                    category_key="supplier_size",
                    message="Supplier-specific size codes can be normalized to the shared taxonomy.",
                    severity="medium",
                    confidence=0.93,
                    metafields=[
                        _metafield(
                            "extractor",
                            "normalized_supplier_size_map",
                            json.dumps({raw: canonical for raw, canonical in supplier_pairs}),
                            "json",
                        ),
                    ],
                    details={
                        "detected_value": [raw for raw, _ in supplier_pairs],
                        "canonical_value": [canonical for _, canonical in supplier_pairs],
                    },
                    shop_domain=shop_domain,
                )
            )

        if children_pairs and allowed("children_size", 0.94):
            suggestions.append(
                _create_normalization_suggestion(
                    product_index=index,
                    product_title=title,
                    category_key="children_size",
                    message="Children sizing formats are mixed; map to a unified children size taxonomy.",
                    severity="medium",
                    confidence=0.94,
                    metafields=[
                        _metafield(
                            "extractor",
                            "normalized_children_size_map",
                            json.dumps({raw: canonical for raw, canonical in children_pairs}),
                            "json",
                        ),
                    ],
                    details={
                        "detected_value": [raw for raw, _ in children_pairs],
                        "canonical_value": [canonical for _, canonical in children_pairs],
                    },
                    shop_domain=shop_domain,
                )
            )

        if has_numeric_size and has_text_size and allowed("structured_unstructured_size", 0.9):
            suggestions.append(
                _create_normalization_suggestion(
                    product_index=index,
                    product_title=title,
                    category_key="structured_unstructured_size",
                    message="Structured and unstructured size formats are mixed; add a canonical size representation.",
                    severity="low",
                    confidence=0.9,
                    metafields=[
                        _metafield("extractor", "normalized_size_format", "hybrid", "single_line_text_field"),
                    ],
                    details={
                        "detected_value": "numeric_and_text_sizes",
                        "canonical_value": "canonical_size_taxonomy",
                    },
                    shop_domain=shop_domain,
                )
            )

        ordered_sizes = [size for size in canonical_sizes if size in _SIZE_ORDER]
        if len(ordered_sizes) >= 2:
            expected = sorted(ordered_sizes, key=lambda item: _SIZE_ORDER.index(item))
            if ordered_sizes != expected and allowed("variant_ordering", 0.95):
                suggestions.append(
                    _create_normalization_suggestion(
                        product_index=index,
                        product_title=title,
                        category_key="variant_ordering",
                        message="Variant order is inconsistent; reorder sizes into canonical progression.",
                        severity="low",
                        confidence=0.95,
                        metafields=[
                            _metafield(
                                "extractor",
                                "normalized_variant_order",
                                json.dumps(expected),
                                "json",
                            ),
                        ],
                        details={
                            "detected_value": ordered_sizes,
                            "canonical_value": expected,
                        },
                        shop_domain=shop_domain,
                    )
                )

        unit_matches = re.findall(r"\b\d+(?:\.\d+)?\s*(inches|inch|in|cm|mm|m)\b", body_lower)
        detected_units = sorted({match for match in unit_matches})
        canonical_unit = "cm" if unit_system == "metric" else "in"
        if len(detected_units) >= 2 and allowed("mixed_units", 0.92):
            suggestions.append(
                _create_normalization_suggestion(
                    product_index=index,
                    product_title=title,
                    category_key="mixed_units",
                    message="Mixed measurement units detected; normalize to one canonical unit system.",
                    severity="medium",
                    confidence=0.92,
                    metafields=[
                        _metafield("extractor", "normalized_unit_system", unit_system),
                        _metafield("extractor", "normalized_dimension_unit", canonical_unit),
                    ],
                    details={
                        "detected_value": detected_units,
                        "canonical_value": canonical_unit,
                    },
                    shop_domain=shop_domain,
                )
            )

        dimension_match = re.search(
            r"\b(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)(?:\s*(in|inch|inches|cm|mm|m))?\b",
            body_lower,
        )
        if dimension_match and allowed("dimensions_format", 0.9):
            h, w, d, unit = dimension_match.groups()
            unit_value = unit or canonical_unit
            canonical_dimension = f"Height: {h} {unit_value}, Width: {w} {unit_value}, Depth: {d} {unit_value}"
            suggestions.append(
                _create_normalization_suggestion(
                    product_index=index,
                    product_title=title,
                    category_key="dimensions_format",
                    message="Dimensions use inconsistent formatting; store canonical labeled dimensions.",
                    severity="low",
                    confidence=0.9,
                    metafields=[
                        _metafield("extractor", "normalized_dimensions", canonical_dimension),
                    ],
                    details={
                        "detected_value": dimension_match.group(0),
                        "canonical_value": canonical_dimension,
                    },
                    shop_domain=shop_domain,
                )
            )

        has_hidden_dimension = bool(
            re.search(r"\b\d+(?:\.\d+)?\s*(inches|inch|in|cm|mm|m)\s*(tall|wide|deep|height|width|depth)\b", body_lower)
        )
        if has_hidden_dimension and allowed("description_extraction", 0.88):
            suggestions.append(
                _create_normalization_suggestion(
                    product_index=index,
                    product_title=title,
                    category_key="description_extraction",
                    message="Sizing/dimensions are embedded in description text; extract to structured metafields.",
                    severity="low",
                    confidence=0.88,
                    metafields=[
                        _metafield("extractor", "normalized_dimensions_extracted", body[:400]),
                    ],
                    details={
                        "detected_value": body[:200],
                        "canonical_value": "structured_metafields",
                    },
                    shop_domain=shop_domain,
                )
            )

        inferred_dimensions = _extract_inferred_dimensions(
            title=title,
            body=body,
            existing_option_names=existing_option_names,
        )
        if inferred_dimensions and allowed("missing_options", 0.86):
            evidence_sources = sorted(
                {
                    source
                    for item in inferred_dimensions
                    for source in item.get("evidence_sources", [])
                    if isinstance(source, str) and source
                }
            )
            variant_operations = _build_variant_operations(
                product=product,
                title=title,
                dimensions=inferred_dimensions,
            )
            if variant_operations.get("create_variants"):
                suggestions.append(
                    _create_normalization_suggestion(
                        product_index=index,
                        product_title=title,
                        category_key="missing_options",
                        message="Detected missing option dimensions; suggest creating normalized product options and variants.",
                        severity="medium",
                        confidence=0.86,
                        metafields=[
                            _metafield(
                                "extractor",
                                "inferred_option_candidates",
                                json.dumps(inferred_dimensions),
                                "json",
                            )
                        ],
                        patch_payload={
                            "variant_operations": variant_operations,
                            "metafields": [
                                _metafield(
                                    "extractor",
                                    "inferred_option_candidates",
                                    json.dumps(inferred_dimensions),
                                    "json",
                                )
                            ],
                        },
                        details={
                            "detected_value": [item.get("detected_values") for item in inferred_dimensions],
                            "canonical_value": [item.get("canonical_values") for item in inferred_dimensions],
                            "evidence_sources": evidence_sources,
                            "inferred_dimensions": inferred_dimensions,
                        },
                        shop_domain=shop_domain,
                    )
                )

    return suggestions


def _build_persisted_suggestions(
    *,
    products: list[dict[str, Any]],
    suggestions: ProductIntelligenceSuggestionsList,
    shop_domain: str,
) -> list[dict[str, Any]]:
    persisted: list[dict[str, Any]] = []
    product_count = len(products)
    for item in suggestions.suggestions:
        if item.product_index < 0 or item.product_index >= product_count:
            raise ValueError(
                f"LLM suggestion product_index out of range: {item.product_index} for {product_count} products"
            )
        source_product = products[item.product_index]
        fallback_title = str(source_product.get("title") or "").strip()
        suggestion_title = str(item.product_title or "").strip() or fallback_title
        if not suggestion_title:
            raise ValueError(
                f"LLM suggestion missing product title for product_index {item.product_index}"
            )
        persisted.append(
            {
                "suggestion_id": str(uuid.uuid4()),
                "finding_id": str(uuid.uuid4()),
                "product_index": item.product_index,
                "product_title": suggestion_title,
                "category": item.category.strip(),
                "severity": item.severity,
                "message": item.message.strip(),
                "patch_payload": item.patch_payload.model_dump(exclude_none=True),
                "details": item.details.model_dump(exclude_none=True) if item.details else {},
                "status": "pending",
                "shop_domain": shop_domain,
            }
        )
    return persisted


async def execute(
    *,
    supabase: SupabaseNamespacedPort,
    products: list[dict[str, Any]],
    shop_domain: str,
    normalization_settings: dict[str, Any] | None = None,
    trace_event: Any | None = None,
) -> list[dict[str, Any]]:
    if not products:
        raise ValueError("No products provided for intelligence suggestion generation")

    if callable(trace_event):
        trace_event(
            phase="suggestions_prepare",
            message="Preparing product intelligence suggestion generation",
            payload_preview={"products_count": len(products)},
        )

    llm_suggestions: list[dict[str, Any]] = []
    model_env = _resolve_model_env(supabase, shop_domain)
    indexed_products = [
        {"_product_index": i, **p} for i, p in enumerate(products)
    ]
    try:
        response = await run_product_intelligence_suggestions(
            products=indexed_products,
            model_env=model_env,
            trace_event=trace_event if callable(trace_event) else None,
        )
        payload = getattr(response, "value", None)
        if payload is not None:
            parsed = _parse_suggestion_payload(payload)
        else:
            response_text = str(getattr(response, "text", "") or "")
            parsed = _parse_suggestion_text(response_text)
        llm_suggestions = _build_persisted_suggestions(
            products=products,
            suggestions=parsed,
            shop_domain=shop_domain,
        )
    except Exception as exc:
        if callable(trace_event):
            trace_event(
                phase="suggestions_llm_skipped",
                message="Skipping LLM suggestions; continuing with deterministic normalization suggestions",
                payload_preview={"error": str(exc)},
                level="warning",
            )

    deterministic_suggestions = _build_normalization_suggestions(
        products=products,
        shop_domain=shop_domain,
        normalization_settings=normalization_settings or _default_normalization_settings(),
    )

    combined = [*llm_suggestions, *deterministic_suggestions]
    if callable(trace_event):
        trace_event(
            phase="suggestions_normalized",
            message="Prepared intelligence suggestions for persistence",
            payload_preview={
                "llm_suggestions_count": len(llm_suggestions),
                "normalization_suggestions_count": len(deterministic_suggestions),
                "suggestions_count": len(combined),
            },
        )
    return combined
