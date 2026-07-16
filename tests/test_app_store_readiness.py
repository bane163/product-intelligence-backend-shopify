import importlib

import pytest
from httpx import ASGITransport, AsyncClient


def _reload_main(monkeypatch, *, allowed_origins: str | None = None, shopify_app_url: str | None = None):
    monkeypatch.setenv("SHOPIFY_STORE", "test-shop.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "token")
    monkeypatch.setenv("SHOPIFY_API_KEY", "key")
    monkeypatch.setenv("SHOPIFY_API_SECRET", "secret")
    if allowed_origins is None:
        monkeypatch.delenv("BACKEND_ALLOWED_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("BACKEND_ALLOWED_ORIGINS", allowed_origins)
    if shopify_app_url is None:
        monkeypatch.delenv("SHOPIFY_APP_URL", raising=False)
    else:
        monkeypatch.setenv("SHOPIFY_APP_URL", shopify_app_url)

    import main as main_module

    return importlib.reload(main_module)


@pytest.mark.asyncio
async def test_health_and_ready_endpoints_report_service_availability(monkeypatch):
    main_module = _reload_main(monkeypatch, shopify_app_url="https://app.example.com")

    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        health = await client.get("/health")
        ready = await client.get("/ready")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "services": ["supabase", "llm", "collabora", "tracing", "shopify"],
    }


def test_cors_uses_explicit_reviewable_origins_instead_of_wildcard(monkeypatch):
    main_module = _reload_main(monkeypatch, shopify_app_url="https://app.example.com")

    cors = next(
        middleware
        for middleware in main_module.app.user_middleware
        if middleware.cls.__name__ == "CORSMiddleware"
    )

    assert cors.kwargs["allow_origins"] == ["https://app.example.com"]


def test_cors_prefers_backend_allowed_origins_when_configured(monkeypatch):
    main_module = _reload_main(
        monkeypatch,
        allowed_origins="https://admin.example.com, https://review.example.com",
        shopify_app_url="https://app.example.com",
    )

    cors = next(
        middleware
        for middleware in main_module.app.user_middleware
        if middleware.cls.__name__ == "CORSMiddleware"
    )

    assert cors.kwargs["allow_origins"] == [
        "https://admin.example.com",
        "https://review.example.com",
    ]
