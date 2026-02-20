from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError

from ai.agent_client import run_product_intelligence_suggestions
from ai.models import ProductIntelligenceSuggestionsList
from application.ports.supabase_port import SupabasePort


def _strip_markdown_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return stripped


def _resolve_model_env(supabase: SupabasePort, shop_domain: str) -> dict[str, str]:
    active_model = supabase.get_active_llm_model_config(shop_domain)
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
                "patch_payload": item.patch_payload,
                "status": "pending",
                "shop_domain": shop_domain,
            }
        )
    return persisted


async def execute(
    *,
    supabase: SupabasePort,
    products: list[dict[str, Any]],
    shop_domain: str,
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
    model_env = _resolve_model_env(supabase, shop_domain)
    response = await run_product_intelligence_suggestions(
        products=products,
        model_env=model_env,
        trace_event=trace_event if callable(trace_event) else None,
    )
    payload = getattr(response, "value", None)
    if payload is not None:
        parsed = _parse_suggestion_payload(payload)
    else:
        response_text = str(getattr(response, "text", "") or "")
        parsed = _parse_suggestion_text(response_text)
    if callable(trace_event):
        trace_event(
            phase="suggestions_parsed",
            message="Parsed product intelligence suggestions from LLM response",
            payload_preview={"suggestions_count": len(parsed.suggestions)},
        )
    if not parsed.suggestions:
        return []
    persisted = _build_persisted_suggestions(
        products=products,
        suggestions=parsed,
        shop_domain=shop_domain,
    )
    if callable(trace_event):
        trace_event(
            phase="suggestions_normalized",
            message="Normalized suggestions for persistence",
            payload_preview={"suggestions_count": len(persisted)},
        )
    return persisted
