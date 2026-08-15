"""Copilot BYOK client for semantic product-intelligence enrichment.

Document extraction lives in ``services.llm_service``. This small companion keeps
the existing enrichment lifecycle on the same Copilot-only, empty-mode runtime.
"""

from __future__ import annotations

import json
from tempfile import TemporaryDirectory
from typing import Any, Callable

from agent_framework.github import GitHubCopilotAgent
from copilot import CopilotClient

from application.ports.llm_port import ModelConfiguration
from services.llm_service import map_copilot_provider, scoped_permission_handler
from shared.token_usage import normalize_reported_token_usage


def _trace(trace_event: Callable[..., Any] | None, **event: Any) -> None:
    if trace_event:
        trace_event(**event)


async def run_product_intelligence_suggestions(
    *,
    products: list[dict[str, Any]],
    model_env: dict[str, str] | None,
    trace_event: Callable[..., Any] | None = None,
):
    env = model_env or {}
    model = ModelConfiguration(
        provider="openai" if "api.openai.com" in str(env.get("OLLAMA_CLOUD_URL", "")) else "ollama/openai-compat",
        base_url=str(env.get("OLLAMA_CLOUD_URL") or ""),
        model_id=str(env.get("OLLAMA_MODEL_ID") or ""),
        api_key=str(env.get("OLLAMA_API_KEY") or ""),
    )
    prompt = (
        "Return only JSON matching ProductIntelligenceSuggestionsList. Analyze these indexed "
        "Shopify products and propose conservative, reviewable catalog improvements. Never "
        "claim source evidence that is not present. Products:\n" + json.dumps(products, ensure_ascii=False)
    )
    image_count = sum(
        1 for product in products for image in (product.get("images") or [])
        if isinstance(image, dict) and str(image.get("src") or "").strip()
    )
    _trace(
        trace_event,
        phase="llm_request",
        message="Calling Copilot BYOK for product intelligence suggestions",
        payload_preview={"products_count": len(products), "image_uris_count": image_count},
    )
    with TemporaryDirectory(prefix="stockpile-intelligence-") as runtime_dir:
        client = CopilotClient(mode="empty", base_directory=runtime_dir, use_logged_in_user=False, env={})
        agent = GitHubCopilotAgent(
            instructions="You return strict JSON only and have no tools or host capabilities.",
            client=client,
            default_options={
                "model": model.model_id,
                "provider": map_copilot_provider(model),
                "available_tools": [],
                "on_permission_request": scoped_permission_handler(frozenset(), trace_event),
            },
        )
        try:
            async with agent:
                response = await agent.run(prompt)
                usage = normalize_reported_token_usage(response)
                if usage is not None:
                    _trace(
                        trace_event,
                        phase="copilot_usage",
                        message="Recorded Copilot product intelligence usage",
                        payload_preview={"usage_reported": True},
                        metadata={
                            "usage": usage,
                            "model_name": model.model_id,
                            "provider": model.provider,
                        },
                    )
        finally:
            await client.stop()
    _trace(
        trace_event,
        phase="llm_response",
        message="Received Copilot product intelligence suggestions",
        payload_preview=str(getattr(response, "text", "") or "")[:700],
    )
    return response
