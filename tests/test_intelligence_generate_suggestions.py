from types import SimpleNamespace

import pytest

import application.use_cases.intelligence_generate_suggestions as intelligence_generate_suggestions_uc
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
