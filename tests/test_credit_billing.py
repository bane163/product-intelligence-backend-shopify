from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from services.credit_billing_service import (
    CreditLimitReached,
    CreditOperationManager,
    Cycle,
    PartnerSubscriptionVerifier,
    ShopifyAppEventsClient,
    SubscriptionVerificationUnavailable,
    drain_app_event_outbox,
    reconcile_delivered_credits,
)
from shared.credit_billing import (
    GPT_5_6_LUNA_V1,
    BillingRateUnavailable,
    ProviderUsage,
    calculate_cost,
    credits_for_raw_cost,
)


def cycle(
    *, included: int = 100, overage: int = 0,
    start: str = "2026-08-01T00:00:00+00:00", mode: str = "shopify",
):
    return Cycle(
        "credit-test.myshopify.com", start, "2026-09-01T00:00:00+00:00",
        included, overage, "starter", mode,
    )


def test_credit_formula_rounds_up_whole_credits():
    assert credits_for_raw_cost(Decimal("0")) == 0
    assert credits_for_raw_cost(Decimal("0.30")) == 125
    assert credits_for_raw_cost(Decimal("0.00001")) == 1


def test_luna_cache_write_cached_and_long_context_pricing():
    regular = calculate_cost(
        usage=ProviderUsage(input_tokens=100_000, cached_input_tokens=50_000,
                            cache_write_tokens=10_000, output_tokens=20_000),
        openai_rate=GPT_5_6_LUNA_V1,
        azure_page_rate_usd=None,
        platform_cost_usd=0,
    )
    # 40k uncached * .20 + 50k cached * .02 + 10k writes * .25 + 20k output * 1.20
    assert regular.openai_cost_usd == Decimal("0.0355")

    long = calculate_cost(
        usage=ProviderUsage(input_tokens=300_000, cached_input_tokens=0, output_tokens=100_000),
        openai_rate=GPT_5_6_LUNA_V1,
        azure_page_rate_usd=None,
        platform_cost_usd=0,
    )
    assert long.openai_cost_usd == Decimal("0.300")


def test_missing_cache_telemetry_is_all_uncached_and_azure_rate_is_required():
    result = calculate_cost(
        usage=ProviderUsage(input_tokens=1_000, cached_input_tokens=None),
        openai_rate=GPT_5_6_LUNA_V1,
        azure_page_rate_usd=None,
        platform_cost_usd=0,
    )
    assert result.openai_cost_usd == Decimal("0.00020")
    with pytest.raises(BillingRateUnavailable):
        calculate_cost(
            usage=ProviderUsage(azure_pages=2), openai_rate=None,
            azure_page_rate_usd=None, platform_cost_usd=0,
        )


def test_reservation_is_concurrent_atomic_and_idempotent():
    manager = CreditOperationManager()

    def reserve(index: int):
        return manager.reserve(
            cycle=cycle(), operation_key=f"key-{index}", operation_type="document_import",
            maximum_credits=60, rate_card_version=GPT_5_6_LUNA_V1.version,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = []
        for future in [pool.submit(reserve, 1), pool.submit(reserve, 2)]:
            try:
                outcomes.append(future.result())
            except CreditLimitReached:
                outcomes.append(None)
    assert sum(item is not None for item in outcomes) == 1

    existing = manager.reserve(
        cycle=cycle(), operation_key="same", operation_type="document_import",
        maximum_credits=20, rate_card_version=GPT_5_6_LUNA_V1.version,
    )
    duplicate = manager.reserve(
        cycle=cycle(), operation_key="same", operation_type="document_import",
        maximum_credits=99, rate_card_version=GPT_5_6_LUNA_V1.version,
    )
    assert duplicate["operation_id"] == existing["operation_id"]
    assert duplicate["reserved_credits"] == 20


def test_settlement_caps_estimator_miss_missing_telemetry_and_reversal():
    manager = CreditOperationManager()
    operation = manager.reserve(
        cycle=cycle(included=200), operation_key="settle", operation_type="document_import",
        maximum_credits=80, rate_card_version=GPT_5_6_LUNA_V1.version,
    )
    settled = manager.settle(
        operation_id=operation["operation_id"], actual_credits=120,
        raw_usage={"input_tokens": 10}, cost_breakdown={"raw_cost_usd": "1"},
    )
    assert settled["settled_credits"] == 80
    assert len(manager.pending_outbox()) == 1
    duplicate = manager.settle(
        operation_id=operation["operation_id"], actual_credits=20,
        raw_usage={}, cost_breakdown={},
    )
    assert duplicate["settled_credits"] == 80
    reversed_operation = manager.reverse(operation["operation_id"], 30, "correction")
    assert reversed_operation["reversed_credits"] == 30
    assert sorted(item["credits"] for item in manager.pending_outbox()) == [-30, 80]

    missing = manager.reserve(
        cycle=cycle(included=200), operation_key="missing", operation_type="document_import",
        maximum_credits=25, rate_card_version=GPT_5_6_LUNA_V1.version,
    )
    result = manager.settle(
        operation_id=missing["operation_id"], actual_credits=None,
        raw_usage={}, cost_breakdown={}, missing_telemetry=True,
    )
    assert result["settled_credits"] == 25


def test_overage_limit_cannot_drop_below_committed_amount():
    manager = CreditOperationManager()
    current = cycle(included=10, overage=50)
    manager.reserve(
        cycle=current, operation_key="overage", operation_type="document_import",
        maximum_credits=30, rate_card_version=GPT_5_6_LUNA_V1.version,
    )
    with pytest.raises(CreditLimitReached):
        manager.set_overage_limit(current, 19)


def test_cycle_rollover_releases_old_reservation_and_caps_new_cycle():
    manager = CreditOperationManager()
    old = cycle(included=100)
    operation = manager.reserve(
        cycle=old, operation_key="rollover", operation_type="document_import",
        maximum_credits=80, rate_card_version=GPT_5_6_LUNA_V1.version,
    )
    new = cycle(included=30, start="2026-09-01T00:00:00+00:00")
    result = manager.settle(
        operation_id=operation["operation_id"], actual_credits=70,
        raw_usage={}, cost_breakdown={}, completion_cycle=new,
    )
    assert result["settled_credits"] == 30
    assert result["cycle_start"] == new.starts_at
    assert manager.summary(old, subscription_state="active", billing_mode="shopify")["reserved_credits"] == 0


def test_delivered_credit_reconciliation_is_explicit():
    assert reconcile_delivered_credits(80, 80)["matches"] is True
    mismatch = reconcile_delivered_credits(80, 75)
    assert mismatch == {"local_credits": 80, "partner_credits": 75, "difference": 5, "matches": False}


@pytest.mark.parametrize("mode", ["simulated", "instrumentation"])
def test_non_shopify_settlements_are_durable_but_never_enter_outbox(mode):
    manager = CreditOperationManager()
    operation = manager.reserve(
        cycle=cycle(mode=mode), operation_key=f"{mode}-operation",
        operation_type="document_import", maximum_credits=3,
        rate_card_version=GPT_5_6_LUNA_V1.version,
        metadata={"filename": "sample.xlsx"},
    )
    settled = manager.settle(
        operation_id=operation["operation_id"], actual_credits=6,
        raw_usage={"input_tokens": 43_062, "output_tokens": 4_232},
        cost_breakdown={"credits": 6},
    )
    assert settled["settled_credits"] == 3
    assert settled["billing_mode"] == mode
    assert manager.pending_outbox() == []


@pytest.mark.asyncio
async def test_partner_cache_and_stale_active_grace(monkeypatch):
    calls = 0

    async def transport(_app: str, _shop: str):
        nonlocal calls
        calls += 1
        return {"data": {"activeSubscription": {"billingPeriod": "MONTHLY"}}}

    verifier = PartnerSubscriptionVerifier(
        app_gid="gid://partners/App/1", organization_id="1", access_token="token",
        transport=transport,
    )
    assert (await verifier.verify("gid://shopify/Shop/1"))["status"] == "active"
    assert (await verifier.verify("gid://shopify/Shop/1"))["status"] == "active"
    assert calls == 1

    cached_at, cached = verifier._cache["gid://shopify/Shop/1"]
    verifier._cache["gid://shopify/Shop/1"] = (
        datetime.now(timezone.utc) - timedelta(minutes=2), cached
    )

    async def failing(_app: str, _shop: str):
        raise RuntimeError("partner unavailable")

    verifier.transport = failing
    assert (await verifier.verify("gid://shopify/Shop/1"))["verification_stale"] is True
    verifier._cache["gid://shopify/Shop/1"] = (
        datetime.now(timezone.utc) - timedelta(minutes=6), cached
    )
    with pytest.raises(SubscriptionVerificationUnavailable):
        await verifier.verify("gid://shopify/Shop/1")


@pytest.mark.asyncio
async def test_app_events_202_delivery_and_409_retry(monkeypatch):
    monkeypatch.setenv("SHOPIFY_EVENT_DELIVERY_ENABLED", "true")
    monkeypatch.setenv("SHOPIFY_API_KEY", "key")
    monkeypatch.setenv("SHOPIFY_API_SECRET", "secret")
    event_status = 202

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal event_status
        if request.url.path == "/auth/access_token":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 300})
        return httpx.Response(event_status)

    manager = CreditOperationManager()
    operation = manager.reserve(
        cycle=cycle(), operation_key="event", operation_type="document_import",
        maximum_credits=10, rate_card_version=GPT_5_6_LUNA_V1.version,
    )
    manager.settle(
        operation_id=operation["operation_id"], actual_credits=5,
        raw_usage={}, cost_breakdown={},
    )
    client = ShopifyAppEventsClient(transport=httpx.MockTransport(handler))
    assert await drain_app_event_outbox(manager, client) == 1
    assert manager._outbox[f"pc:v1:{operation['operation_id']}:s"]["status"] == "delivered"

    second = manager.reserve(
        cycle=cycle(), operation_key="conflict", operation_type="document_import",
        maximum_credits=10, rate_card_version=GPT_5_6_LUNA_V1.version,
    )
    manager.settle(operation_id=second["operation_id"], actual_credits=5, raw_usage={}, cost_breakdown={})
    event_status = 409
    assert await drain_app_event_outbox(manager, client, max_attempts=2) == 0
    assert manager._outbox[f"pc:v1:{second['operation_id']}:s"]["status"] == "retry"
