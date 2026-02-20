from types import SimpleNamespace

import pytest

from application.use_cases.intelligence_generate_suggestions import execute


class _FakeSupabase:
    def __init__(self, config):
        self._config = config

    def get_active_llm_model_config(self, shop_domain: str):
        _ = shop_domain
        return self._config


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
    assert captured["products"] == products
    assert captured["model_env"]["OLLAMA_MODEL_ID"] == "deepseek-r1:8b"


@pytest.mark.asyncio
async def test_generate_suggestions_requires_active_model_config():
    supabase = _FakeSupabase(None)
    with pytest.raises(ValueError, match="No active LLM model config"):
        await execute(
            supabase=supabase,
            products=[{"id": "gid://shopify/Product/1", "title": "Catalog Product"}],
            shop_domain="store.myshopify.com",
        )
