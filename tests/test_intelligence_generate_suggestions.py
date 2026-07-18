from types import SimpleNamespace

import pytest

from ai.models import ProductIntelligenceSuggestionsList
import application.use_cases.intelligence_generate_suggestions as intelligence_generate_suggestions_uc
from application.use_cases.intelligence_generate_suggestions import execute


class _FakeSupabase:
    def __init__(self, config):
        self._config = config
        self.llm_configs = self

    def get_active_llm_model_config(self, shop_domain: str):
        _ = shop_domain
        return self._config


def _find_non_strict_object_nodes(node, path: str = "root") -> list[tuple[str, object]]:
    violations: list[tuple[str, object]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            additional_properties = node.get("additionalProperties", "__MISSING__")
            if additional_properties is not False:
                violations.append((path, additional_properties))
        for key, value in node.items():
            violations.extend(_find_non_strict_object_nodes(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            violations.extend(_find_non_strict_object_nodes(item, f"{path}[{index}]"))
    return violations


def test_product_intelligence_schema_is_openai_strict_compatible():
    schema = ProductIntelligenceSuggestionsList.model_json_schema()
    violations = _find_non_strict_object_nodes(schema)
    assert violations == []


@pytest.mark.asyncio
async def test_generate_suggestions_from_llm_payload(monkeypatch):
    captured = {}

    async def fake_run_product_intelligence_suggestions(*, products, model_env, trace_event=None):
        _ = trace_event
        captured["products"] = products
        captured["model_env"] = model_env
        return SimpleNamespace(
            value={
                "suggestions": [
                    {
                        "product_index": 0,
                        "product_title": "Catalog Product",
                        "category": "seo_readiness",
                        "severity": "medium",
                        "message": "Improve SEO title",
                        "patch_payload": {"seo_title": "Better Catalog Product"},
                    }
                ]
            },
            text="",
        )

    monkeypatch.setattr(
        "application.use_cases.intelligence_generate_suggestions.run_product_intelligence_suggestions",
        fake_run_product_intelligence_suggestions,
    )
    supabase = _FakeSupabase(
        {
            "base_url": "http://localhost:11434/v1/",
            "model_id": "deepseek-r1:8b",
            "api_key": "secret",
        }
    )
    products = [{"id": "gid://shopify/Product/1", "title": "Catalog Product"}]
    suggestions = await execute(
        supabase=supabase,
        products=products,
        shop_domain="store.myshopify.com",
    )

    assert len(suggestions) == 1
    assert suggestions[0]["product_index"] == 0
    assert suggestions[0]["category"] == "seo_readiness"
    assert suggestions[0]["patch_payload"] == {"seo_title": "Better Catalog Product"}
    assert suggestions[0]["shop_domain"] == "store.myshopify.com"
    assert captured["products"] == [{"_product_index": 0, **p} for p in products]
    assert captured["model_env"]["OLLAMA_MODEL_ID"] == "deepseek-r1:8b"


@pytest.mark.asyncio
async def test_generate_suggestions_persists_nested_payload_and_details(monkeypatch):
    async def fake_run_product_intelligence_suggestions(*, products, model_env, trace_event=None):
        _ = products, model_env, trace_event
        return SimpleNamespace(
            value={
                "suggestions": [
                    {
                        "product_index": 0,
                        "product_title": "Catalog Product",
                        "category": "normalization_missing_options",
                        "severity": "medium",
                        "message": "Add inferred option dimensions",
                        "patch_payload": {
                            "metafields": [
                                {
                                    "namespace": "extractor",
                                    "key": "inferred_option_candidates",
                                    "type": "json",
                                    "value": "{\"dimension\":\"Size\"}",
                                }
                            ],
                            "variant_operations": {
                                "create_options": [{"name": "Size", "values": ["S", "M"]}],
                                "create_variants": [
                                    {
                                        "option_values": [
                                            {"option_name": "Size", "name": "S"}
                                        ],
                                        "sku": "SKU-S",
                                    }
                                ],
                                "defaults": {
                                    "copy_from_first_variant": True,
                                    "requires_review": True,
                                },
                            },
                        },
                        "details": {
                            "confidence": 0.92,
                            "rule_id": "missing_options",
                            "evidence_sources": ["title", "image"],
                            "inferred_dimensions": [
                                {
                                    "dimension": "Size",
                                        "detected_values": ["Small", "Medium"],
                                        "canonical_values": ["S", "M"],
                                    "confidence": 0.92,
                                    "source": "title",
                                }
                            ],
                        },
                    }
                ]
            },
            text="",
        )

    monkeypatch.setattr(
        "application.use_cases.intelligence_generate_suggestions.run_product_intelligence_suggestions",
        fake_run_product_intelligence_suggestions,
    )
    supabase = _FakeSupabase(
        {
            "base_url": "http://localhost:11434/v1/",
            "model_id": "deepseek-r1:8b",
            "api_key": "secret",
        }
    )
    suggestions = await execute(
        supabase=supabase,
        products=[{"id": "gid://shopify/Product/1", "title": "Catalog Product", "variants": [{"title": "Default Title", "sku": "SKU", "price": "10.00"}]}],
        shop_domain="store.myshopify.com",
    )

    assert suggestions[0]["patch_payload"]["variant_operations"]["create_options"][0]["name"] == "Size"
    assert suggestions[0]["details"]["evidence_sources"] == ["title", "image"]
    assert suggestions[0]["details"]["inferred_dimensions"][0]["canonical_values"] == ["S", "M"]
    assert len(suggestions[0]["patch_payload"]["variant_operations"]["create_variants"]) == 2


@pytest.mark.asyncio
async def test_generate_suggestions_requires_active_model_config():
    supabase = _FakeSupabase(None)
    with pytest.raises(ValueError, match="No active LLM model config"):
        await execute(
            supabase=supabase,
            products=[{"id": "gid://shopify/Product/1", "title": "Catalog Product"}],
            shop_domain="store.myshopify.com",
        )


@pytest.mark.asyncio
async def test_product_intelligence_suggestions_trace_payload_uses_image_uris(monkeypatch):
    traces = []
    run_product_intelligence_suggestions = (
        intelligence_generate_suggestions_uc.run_product_intelligence_suggestions
    )

    class _FakeAgent:
        async def run(self, user_message):
            _ = user_message
            return SimpleNamespace(value={"suggestions": []}, text="", usage=None)

    class _FakeClient:
        def create_agent(self, **kwargs):
            _ = kwargs
            return _FakeAgent()

    monkeypatch.setitem(
        run_product_intelligence_suggestions.__globals__,
        "_create_chat_client",
        lambda model_env: _FakeClient(),
    )
    monkeypatch.setitem(
        run_product_intelligence_suggestions.__globals__,
        "render_prompt",
        lambda template, **kwargs: "instructions" if "instructions" in template else "user prompt",
    )

    response = await run_product_intelligence_suggestions(
        products=[{"title": "Catalog Product", "images": [{"src": "data:image/png;base64,AAAA"}]}],
        model_env={"OLLAMA_API_KEY": "secret"},
        trace_event=lambda **kwargs: traces.append(kwargs),
    )

    assert response.value == {"suggestions": []}
    request_trace = next(item for item in traces if item.get("phase") == "llm_request")
    assert request_trace["payload_preview"]["image_uris_count"] == 1


@pytest.mark.asyncio
async def test_llm_receives_products_with_explicit_index_annotations(monkeypatch):
    """Products sent to the LLM must include _product_index so the model
    can reliably map suggestions back to the correct product."""
    captured = {}

    async def fake_run(*, products, model_env, trace_event=None):
        captured["products"] = products
        return SimpleNamespace(value={"suggestions": []}, text="")

    monkeypatch.setattr(
        "application.use_cases.intelligence_generate_suggestions.run_product_intelligence_suggestions",
        fake_run,
    )
    supabase = _FakeSupabase(
        {"base_url": "http://localhost:11434/v1/", "model_id": "deepseek-r1:8b", "api_key": "s"}
    )
    products = [
        {"title": "Product A"},
        {"title": "Product B"},
        {"title": "Product C"},
    ]
    await execute(supabase=supabase, products=products, shop_domain="store.myshopify.com")

    sent = captured["products"]
    assert len(sent) == 3
    for idx, item in enumerate(sent):
        assert item["_product_index"] == idx, (
            f"Product at position {idx} should have _product_index={idx}"
        )


@pytest.mark.asyncio
async def test_multi_product_llm_suggestions_preserve_distinct_indices(monkeypatch):
    """LLM suggestions targeting different products must keep their distinct
    product_index values through the persistence layer."""

    async def fake_run(*, products, model_env, trace_event=None):
        return SimpleNamespace(
            value={
                "suggestions": [
                    {
                        "product_index": 0,
                        "product_title": "Product A",
                        "category": "seo_readiness",
                        "severity": "low",
                        "message": "Improve SEO for A",
                        "patch_payload": {"seo_title": "Better A"},
                    },
                    {
                        "product_index": 1,
                        "product_title": "Product B",
                        "category": "seo_readiness",
                        "severity": "low",
                        "message": "Improve SEO for B",
                        "patch_payload": {"seo_title": "Better B"},
                    },
                    {
                        "product_index": 2,
                        "product_title": "Product C",
                        "category": "completeness",
                        "severity": "medium",
                        "message": "Add vendor for C",
                        "patch_payload": {"vendor": "Acme"},
                    },
                ]
            },
            text="",
        )

    monkeypatch.setattr(
        "application.use_cases.intelligence_generate_suggestions.run_product_intelligence_suggestions",
        fake_run,
    )
    supabase = _FakeSupabase(
        {"base_url": "http://localhost:11434/v1/", "model_id": "deepseek-r1:8b", "api_key": "s"}
    )
    products = [
        {"title": "Product A"},
        {"title": "Product B"},
        {"title": "Product C"},
    ]
    suggestions = await execute(supabase=supabase, products=products, shop_domain="store.myshopify.com")

    llm_suggestions = [s for s in suggestions if not s["category"].startswith("normalization_")]
    assert len(llm_suggestions) == 3
    indices = [s["product_index"] for s in llm_suggestions]
    assert indices == [0, 1, 2], f"Expected distinct indices [0,1,2], got {indices}"
