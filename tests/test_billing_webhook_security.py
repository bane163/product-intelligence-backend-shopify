import os
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("SHOPIFY_STORE", "test-shop.myshopify.com")
os.environ.setdefault("SHOPIFY_ACCESS_TOKEN", "token")

from app_context import AppContext, ServiceRegistry
from main import app


class _FakeBillingService:
    def __init__(self) -> None:
        self.synced: list[tuple[str, dict]] = []
        self.events: list[tuple[str, str, dict]] = []

    def upsert_subscription(self, shop_domain: str, subscription: dict):
        self.synced.append((shop_domain, dict(subscription)))
        return {"shop_domain": shop_domain, **subscription}

    def record_billing_event(self, shop_domain: str, event_type: str, event_data: dict):
        self.events.append((shop_domain, event_type, dict(event_data)))
        return {"shop_domain": shop_domain, "event_type": event_type}


@pytest.mark.asyncio
async def test_subscription_sync_requires_authenticated_shop_header() -> None:
    fake_service = _FakeBillingService()
    fake_ctx = AppContext(
        services=ServiceRegistry(
            supabase=SimpleNamespace(_service=fake_service),
            llm=SimpleNamespace(),
            collabora=SimpleNamespace(),
            tracing=SimpleNamespace(),
            shopify=SimpleNamespace(),
        )
    )

    from api.agents import billing as billing_module

    app.dependency_overrides[billing_module.get_ctx] = lambda: fake_ctx
    original_get_billing_svc = billing_module._get_billing_svc
    billing_module._get_billing_svc = lambda _ctx: fake_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/agents/billing/subscription/sync",
            json={
                "shop_domain": "store.myshopify.com",
                "subscription": {"status": "active", "plan_name": "Growth"},
            },
        )

    billing_module._get_billing_svc = original_get_billing_svc
    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert fake_service.synced == []
    assert fake_service.events == []


@pytest.mark.asyncio
async def test_subscription_sync_accepts_matching_authenticated_shop_header() -> None:
    fake_service = _FakeBillingService()
    fake_ctx = AppContext(
        services=ServiceRegistry(
            supabase=SimpleNamespace(_service=fake_service),
            llm=SimpleNamespace(),
            collabora=SimpleNamespace(),
            tracing=SimpleNamespace(),
            shopify=SimpleNamespace(),
        )
    )

    from api.agents import billing as billing_module

    app.dependency_overrides[billing_module.get_ctx] = lambda: fake_ctx
    original_get_billing_svc = billing_module._get_billing_svc
    billing_module._get_billing_svc = lambda _ctx: fake_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/agents/billing/subscription/sync",
            headers={"x-shop-domain": "store.myshopify.com"},
            json={
                "shop_domain": "store.myshopify.com",
                "subscription": {"status": "active", "plan_name": "Growth"},
            },
        )

    billing_module._get_billing_svc = original_get_billing_svc
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert fake_service.synced == [
        ("store.myshopify.com", {"status": "active", "plan_name": "Growth"})
    ]
    assert fake_service.events == [
        (
            "store.myshopify.com",
            "subscription_synced",
            {"status": "active", "plan_name": "Growth"},
        )
    ]
