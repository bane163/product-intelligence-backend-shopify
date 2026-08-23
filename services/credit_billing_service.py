"""Atomic credit lifecycle, Shopify verification, and App Events helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
from threading import RLock
from typing import Any, Awaitable, Callable
import uuid

import httpx

from shared.credit_billing import PLANS


LOG = logging.getLogger(__name__)
TERMINAL_OPERATION_STATUSES = {"settled", "released", "reversed"}


class CreditLimitReached(ValueError):
    code = "CREDIT_LIMIT_REACHED"


class SubscriptionRequired(ValueError):
    code = "SUBSCRIPTION_REQUIRED"


class SubscriptionVerificationUnavailable(RuntimeError):
    code = "SUBSCRIPTION_VERIFICATION_UNAVAILABLE"


class BillingStorageUnavailable(RuntimeError):
    code = "BILLING_STORAGE_UNAVAILABLE"


@dataclass(frozen=True)
class BillingAccessContext:
    mode: str
    plan_handle: str


@dataclass(frozen=True)
class Cycle:
    shop_domain: str
    starts_at: str
    ends_at: str
    included_credits: int
    overage_limit_cents: int
    plan_handle: str
    billing_mode: str = "shopify"


class CreditOperationManager:
    """Thread-safe fallback with the same semantics as the database RPCs."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._cycles: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._operations: dict[str, dict[str, Any]] = {}
        self._usage_events: dict[tuple[str, str], dict[str, Any]] = {}
        self._ledger: list[dict[str, Any]] = []
        self._outbox: dict[str, dict[str, Any]] = {}

    def _cycle(self, cycle: Cycle) -> dict[str, Any]:
        key = (cycle.shop_domain, cycle.billing_mode, cycle.starts_at)
        record = self._cycles.setdefault(
            key,
            {
                "shop_domain": cycle.shop_domain,
                "starts_at": cycle.starts_at,
                "ends_at": cycle.ends_at,
                "plan_handle": cycle.plan_handle,
                "billing_mode": cycle.billing_mode,
                "included_credits": cycle.included_credits,
                "overage_limit_cents": cycle.overage_limit_cents,
                "reserved_credits": 0,
                "settled_credits": 0,
                "reversed_credits": 0,
            },
        )
        record.update(
            ends_at=cycle.ends_at,
            plan_handle=cycle.plan_handle,
            included_credits=cycle.included_credits,
        )
        return record

    def reserve(
        self,
        *,
        cycle: Cycle,
        operation_key: str,
        operation_type: str,
        maximum_credits: int,
        rate_card_version: str,
        metadata: dict[str, Any] | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        maximum_credits = max(0, int(maximum_credits))
        with self._lock:
            existing = next(
                (
                    item
                    for item in self._operations.values()
                    if item["shop_domain"] == cycle.shop_domain
                    and item["operation_key"] == operation_key
                ),
                None,
            )
            if existing:
                return dict(existing)
            current = self._cycle(cycle)
            committed = (
                current["reserved_credits"]
                + current["settled_credits"]
                - current["reversed_credits"]
            )
            capacity = current["included_credits"] + current["overage_limit_cents"]
            if committed + maximum_credits > capacity:
                raise CreditLimitReached(
                    f"This operation needs up to {maximum_credits} credits; "
                    f"{max(0, capacity - committed)} remain within the monthly limit"
                )
            identifier = operation_id or str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            operation = {
                "operation_id": identifier,
                "operation_key": operation_key,
                "operation_type": operation_type,
                "shop_domain": cycle.shop_domain,
                "billing_mode": cycle.billing_mode,
                "plan_handle": cycle.plan_handle,
                "cycle_start": cycle.starts_at,
                "cycle_end": cycle.ends_at,
                "status": "reserved",
                "reserved_credits": maximum_credits,
                "settled_credits": 0,
                "maximum_charge_cents": maximum_credits,
                "rate_card_version": rate_card_version,
                "raw_usage": {},
                "cost_breakdown": {},
                "metadata": dict(metadata or {}),
                "created_at": now,
                "updated_at": now,
            }
            self._operations[identifier] = operation
            current["reserved_credits"] += maximum_credits
            self._ledger.append(
                {"operation_id": identifier, "entry_type": "reserve", "credits": maximum_credits, "created_at": now}
            )
            return dict(operation)

    def record_provider_usage(
        self,
        *,
        operation_id: str,
        provider_call_key: str,
        provider: str,
        model: str | None,
        usage: dict[str, Any],
        succeeded: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            key = (operation_id, provider_call_key)
            existing = self._usage_events.get(key)
            if existing:
                return dict(existing)
            if operation_id not in self._operations:
                raise LookupError("Unknown billing operation")
            record = {
                "operation_id": operation_id,
                "provider_call_key": provider_call_key,
                "provider": provider,
                "model": model,
                "usage": dict(usage),
                "succeeded": bool(succeeded),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._usage_events[key] = record
            return dict(record)

    def settle(
        self,
        *,
        operation_id: str,
        actual_credits: int | None,
        raw_usage: dict[str, Any],
        cost_breakdown: dict[str, Any],
        missing_telemetry: bool = False,
        completion_cycle: Cycle | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            operation = self._operations.get(operation_id)
            if not operation:
                raise LookupError("Unknown billing operation")
            if operation["status"] == "settled":
                return dict(operation)
            if operation["status"] in {"released", "reversed"}:
                return dict(operation)
            reserved = int(operation["reserved_credits"])
            requested = reserved if missing_telemetry or actual_credits is None else max(0, int(actual_credits))
            settled = min(reserved, requested)
            if requested > reserved:
                LOG.error(
                    "billing_estimator_miss operation_id=%s reserved=%s actual=%s absorbed=%s",
                    operation_id,
                    reserved,
                    requested,
                    requested - reserved,
                )
            cycle = self._cycles[(operation["shop_domain"], operation["billing_mode"], operation["cycle_start"])]
            cycle["reserved_credits"] -= reserved
            if completion_cycle and completion_cycle.starts_at != operation["cycle_start"]:
                target = self._cycle(completion_cycle)
                capacity = target["included_credits"] + target["overage_limit_cents"]
                committed = target["reserved_credits"] + target["settled_credits"] - target["reversed_credits"]
                settled = min(settled, max(0, capacity - committed))
                operation.update(cycle_start=completion_cycle.starts_at, cycle_end=completion_cycle.ends_at)
                cycle = target
            cycle["settled_credits"] += settled
            operation.update(
                status="settled",
                settled_credits=settled,
                raw_usage=dict(raw_usage),
                cost_breakdown=dict(cost_breakdown),
                missing_telemetry=missing_telemetry,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            self._ledger.append(
                {"operation_id": operation_id, "entry_type": "settle", "credits": settled, "created_at": operation["updated_at"]}
            )
            if settled and operation["billing_mode"] == "shopify":
                event_key = f"pc:v1:{operation_id}:s"
                self._outbox.setdefault(
                    event_key,
                    {
                        "event_key": event_key,
                        "operation_id": operation_id,
                        "shop_domain": operation["shop_domain"],
                        "credits": settled,
                        "status": "pending",
                        "attempts": 0,
                    },
                )
            return dict(operation)

    def release(self, operation_id: str, reason: str) -> dict[str, Any]:
        with self._lock:
            operation = self._operations.get(operation_id)
            if not operation:
                raise LookupError("Unknown billing operation")
            if operation["status"] in TERMINAL_OPERATION_STATUSES:
                return dict(operation)
            reserved = int(operation["reserved_credits"])
            cycle = self._cycles[(operation["shop_domain"], operation["billing_mode"], operation["cycle_start"])]
            cycle["reserved_credits"] -= reserved
            operation.update(status="released", release_reason=reason, updated_at=datetime.now(timezone.utc).isoformat())
            self._ledger.append(
                {"operation_id": operation_id, "entry_type": "release", "credits": -reserved, "created_at": operation["updated_at"]}
            )
            return dict(operation)

    def reverse(self, operation_id: str, credits: int, reason: str) -> dict[str, Any]:
        with self._lock:
            operation = self._operations.get(operation_id)
            if not operation or operation["status"] not in {"settled", "reversed"}:
                raise ValueError("Only settled operations can be reversed")
            already = int(operation.get("reversed_credits") or 0)
            amount = min(max(0, int(credits)), int(operation["settled_credits"]) - already)
            if not amount:
                return dict(operation)
            cycle = self._cycles[(operation["shop_domain"], operation["billing_mode"], operation["cycle_start"])]
            cycle["reversed_credits"] += amount
            operation.update(
                status="reversed" if already + amount == int(operation["settled_credits"]) else "settled",
                reversed_credits=already + amount,
                reversal_reason=reason,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            if operation["billing_mode"] == "shopify":
                event_key = f"pc:v1:{operation_id}:r{already + amount}"
                self._outbox.setdefault(
                    event_key,
                    {
                        "event_key": event_key,
                        "operation_id": operation_id,
                        "shop_domain": operation["shop_domain"],
                        "credits": -amount,
                        "status": "pending",
                        "attempts": 0,
                    },
                )
            self._ledger.append(
                {"operation_id": operation_id, "entry_type": "reverse", "credits": -amount, "created_at": operation["updated_at"]}
            )
            return dict(operation)

    def set_overage_limit(self, cycle: Cycle, cents: int) -> dict[str, Any]:
        cents = int(cents)
        if not 0 <= cents <= 50_000:
            raise ValueError("overage_limit_cents must be between 0 and 50000")
        with self._lock:
            record = self._cycle(cycle)
            committed = record["reserved_credits"] + record["settled_credits"] - record["reversed_credits"]
            committed_overage = max(0, committed - record["included_credits"])
            if cents < committed_overage:
                raise CreditLimitReached(
                    f"Limit cannot be lower than {committed_overage} cents already committed"
                )
            record["overage_limit_cents"] = cents
            return dict(record)

    def summary(self, cycle: Cycle, *, subscription_state: str, billing_mode: str) -> dict[str, Any]:
        with self._lock:
            record = dict(self._cycle(cycle))
            committed = record["settled_credits"] - record["reversed_credits"]
            total_committed = committed + record["reserved_credits"]
            record.update(
                subscription_state=subscription_state,
                billing_mode=billing_mode,
                settled_credits=committed,
                remaining_credits=max(0, record["included_credits"] - total_committed),
                overage_spent_cents=max(0, committed - record["included_credits"]),
                overage_committed_cents=max(0, total_committed - record["included_credits"]),
                maximum_overage_cents=record["overage_limit_cents"],
                history=[
                    dict(item)
                    for item in self._operations.values()
                    if item["shop_domain"] == cycle.shop_domain and item["cycle_start"] == cycle.starts_at
                    and item["billing_mode"] == cycle.billing_mode
                ],
            )
            return record

    def pending_outbox(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._outbox.values() if item["status"] in {"pending", "retry"}]

    def mark_outbox(self, event_key: str, status: str, error: str | None = None) -> None:
        with self._lock:
            item = self._outbox[event_key]
            item.update(status=status, last_error=error, attempts=int(item["attempts"]) + 1)


class PartnerSubscriptionVerifier:
    """60-second Partner API cache with a five-minute stale-active grace."""

    def __init__(
        self,
        *,
        app_gid: str | None = None,
        organization_id: str | None = None,
        access_token: str | None = None,
        transport: Callable[[str, str], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.app_gid = app_gid or os.getenv("SHOPIFY_APP_GID", "").strip()
        self.organization_id = organization_id or os.getenv("SHOPIFY_PARTNER_ORG_ID", "").strip()
        self.access_token = access_token or os.getenv("SHOPIFY_PARTNER_API_ACCESS_TOKEN", "").strip()
        self.transport = transport or self._query
        self._cache: dict[str, tuple[datetime, dict[str, Any]]] = {}

    async def verify(self, shop_gid: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        cached = self._cache.get(shop_gid)
        if cached and now - cached[0] <= timedelta(seconds=60):
            return dict(cached[1])
        try:
            result = await self.transport(self.app_gid, shop_gid)
            normalized = self._normalize(result)
            self._cache[shop_gid] = (now, normalized)
            return dict(normalized)
        except Exception as exc:
            if cached and cached[1].get("status") == "active" and now - cached[0] <= timedelta(minutes=5):
                return {**cached[1], "verification_stale": True}
            raise SubscriptionVerificationUnavailable(
                "Shopify subscription verification is temporarily unavailable"
            ) from exc

    @staticmethod
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        subscription = data.get("activeSubscription") if isinstance(data, dict) else None
        if not isinstance(subscription, dict):
            return {"status": "inactive", "plan_handle": None}
        handle = str(subscription.get("planHandle") or subscription.get("handle") or "").lower()
        return {
            "status": "active",
            "plan_handle": handle if handle in PLANS else None,
            "current_period_start": subscription.get("currentPeriodStart"),
            "current_period_end": subscription.get("currentPeriodEnd"),
            "shopify_subscription_id": subscription.get("id"),
            "raw": subscription,
        }

    async def _query(self, app_gid: str, shop_gid: str) -> dict[str, Any]:
        if not app_gid or not self.organization_id or not self.access_token or not shop_gid:
            raise RuntimeError("Partner API billing configuration is incomplete")
        query = """query ActiveSubscription($appId: ID!, $shopId: ID!) {
          activeSubscription(appId: $appId, shopId: $shopId) { billingPeriod }
        }"""
        url = f"https://partners.shopify.com/{self.organization_id}/api/unstable/graphql.json"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                url,
                headers={"X-Shopify-Access-Token": self.access_token},
                json={"query": query, "variables": {"appId": app_gid, "shopId": shop_gid}},
            )
            response.raise_for_status()
            body = response.json()
            if body.get("errors"):
                raise RuntimeError("Partner API returned GraphQL errors")
            return body


class ShopifyAppEventsClient:
    """Token-caching App Events sender. Payloads contain IDs and quantities only."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.client_id = os.getenv("SHOPIFY_API_KEY", "").strip()
        self.client_secret = os.getenv("SHOPIFY_API_SECRET", "").strip()
        self.delivery_enabled = os.getenv("SHOPIFY_EVENT_DELIVERY_ENABLED", "false").lower() == "true"
        self.transport = transport
        self._token: str | None = None
        self._token_expires_at = datetime.min.replace(tzinfo=timezone.utc)

    async def _bearer_token(self, client: httpx.AsyncClient) -> str:
        now = datetime.now(timezone.utc)
        if self._token and now < self._token_expires_at - timedelta(seconds=30):
            return self._token
        response = await client.post(
            "https://api.shopify.com/auth/access_token",
            json={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        response.raise_for_status()
        payload = response.json()
        self._token = str(payload["access_token"])
        self._token_expires_at = now + timedelta(seconds=max(60, int(payload.get("expires_in") or 300)))
        return self._token

    async def send(self, event: dict[str, Any]) -> int:
        if not self.delivery_enabled:
            return 204
        async with httpx.AsyncClient(timeout=10, transport=self.transport) as client:
            token = await self._bearer_token(client)
            response = await client.post(
                "https://api.shopify.com/app/unstable/events",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "shop_id": event.get("shopify_shop_gid") or event.get("shop_gid"),
                    "event_handle": "processing_credits",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "idempotency_key": event["event_key"],
                    "attributes": {"value": str(int(event["credits"]))},
                },
            )
            if response.status_code not in {200, 201, 202, 204}:
                response.raise_for_status()
            return response.status_code


async def drain_app_event_outbox(
    manager: Any,
    client: ShopifyAppEventsClient,
    *,
    max_attempts: int = 5,
    shop_domain: str | None = None,
) -> int:
    delivered = 0
    pending = manager.pending_shopify_events(shop_domain) if hasattr(manager, "pending_shopify_events") else manager.pending_outbox()
    if shop_domain and not hasattr(manager, "pending_shopify_events"):
        pending = [event for event in pending if event.get("shop_domain") == shop_domain]
    marker = manager.mark_shopify_event if hasattr(manager, "mark_shopify_event") else manager.mark_outbox
    for event in pending:
        try:
            status = await client.send(event)
            marker(event["event_key"], "delivered")
            delivered += 1
            if status == 202:
                LOG.info("Shopify App Event accepted but awaiting asynchronous validation key=%s", event["event_key"])
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in {409, 429} or exc.response.status_code >= 500
            attempts = int(event.get("attempts") or 0) + 1
            marker(event["event_key"], "retry" if retryable and attempts < max_attempts else "failed", type(exc).__name__)
            if retryable and attempts < max_attempts:
                await asyncio.sleep(min(2 ** (attempts - 1), 8))
    return delivered


def reconcile_delivered_credits(local_credits: int, partner_credits: int) -> dict[str, Any]:
    """Return a PII-free reconciliation result suitable for alerting."""
    difference = int(local_credits) - int(partner_credits)
    return {
        "local_credits": int(local_credits),
        "partner_credits": int(partner_credits),
        "difference": difference,
        "matches": difference == 0,
    }
