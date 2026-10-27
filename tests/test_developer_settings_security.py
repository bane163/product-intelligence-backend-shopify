from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from main import app
from app_context import get_ctx
from services.supabase_service import SupabaseService


@pytest.mark.asyncio
async def test_model_routes_require_service_auth_and_scope_updates(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", "secret")
    service = SupabaseService()
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    record = service.create_llm_model_config(shop_domain="other.myshopify.com", name="Other", provider="openai", base_url="https://example.com", model_id="custom", api_key="key")
    from infrastructure.adapters.supabase_adapter import SupabaseAdapter
    app.dependency_overrides[get_ctx] = lambda: SimpleNamespace(supabase=SupabaseAdapter(service))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/agents/llm-configs", params={"shop_domain": "other.myshopify.com"})).status_code == 401
            headers = {"x-stockpile-service-key": "secret", "x-shop-domain": "mine.myshopify.com"}
            response = await client.patch(f"/agents/llm-configs/{record['id']}", headers=headers, json={"model_id": "hijack"})
            assert response.status_code == 404
            assert service.llm_model_configs[record['id']]["model_id"] == "custom"
    finally:
        app.dependency_overrides.pop(get_ctx, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("environment, expected", [("production", 0.8), ("development", 0.2)])
async def test_normalization_save_preserves_production_threshold(monkeypatch, environment, expected):
    monkeypatch.setenv("NODE_ENV", environment)
    class Settings:
        def get_product_intelligence_normalization_settings(self, **kwargs):
            return {"confidence_threshold": 0.8, "unit_system": "metric", "categories": {}}
        def upsert_product_intelligence_normalization_settings(self, **kwargs):
            self.saved = kwargs["settings"]
            return self.saved
    settings = Settings()
    app.dependency_overrides[get_ctx] = lambda: SimpleNamespace(supabase=SimpleNamespace(intelligence=settings))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/agents/intelligence/normalization-settings", headers={"x-shop-domain": "mine.myshopify.com"}, json={"settings": {"confidence_threshold": 0.2, "unit_system": "imperial"}})
            assert response.status_code == 200
            assert settings.saved["confidence_threshold"] == expected
            assert settings.saved["unit_system"] == "imperial"
    finally:
        app.dependency_overrides.pop(get_ctx, None)
