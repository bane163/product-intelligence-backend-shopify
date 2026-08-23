"""Processing-credit billing and canonical Shopify subscription routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app_context import AppContext, get_ctx
from services.credit_billing_service import (
    BillingAccessContext,
    BillingStorageUnavailable,
    CreditLimitReached,
    ShopifyAppEventsClient,
    SubscriptionVerificationUnavailable,
    drain_app_event_outbox,
)
from services.supabase_service import SupabaseService
from shared.credit_billing import PLANS

from .utils import (
    require_authenticated_shop_domain,
    require_shop_domain,
    resolve_dev_billing_simulator_plan,
)


router = APIRouter()


def _get_billing_svc(ctx: AppContext) -> SupabaseService:
    svc = getattr(ctx.supabase, "_svc", None) or getattr(ctx.supabase, "_service", None)
    return svc if isinstance(svc, SupabaseService) else SupabaseService()


def _billing_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _hosted_plan_url(shop_domain: str) -> str:
    store_handle = shop_domain.removesuffix(".myshopify.com")
    app_handle = os.getenv("SHOPIFY_APP_HANDLE", "stockpile").strip() or "stockpile"
    return f"https://admin.shopify.com/store/{store_handle}/charges/{app_handle}/pricing_plans"


def _inactive_summary(shop_domain: str) -> dict[str, Any]:
    return {
        "subscription_state": "inactive",
        "billing_mode": "shopify",
        "plan_handle": None,
        "starts_at": None,
        "ends_at": None,
        "included_credits": 0,
        "settled_credits": 0,
        "reserved_credits": 0,
        "remaining_credits": 0,
        "overage_spent_cents": 0,
        "overage_committed_cents": 0,
        "overage_limit_cents": 0,
        "history": [],
        "hosted_plan_url": _hosted_plan_url(shop_domain),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _refresh_subscription(
    svc: SupabaseService,
    shop_domain: str,
    *,
    redirect_plan_handle: str | None = None,
    shop_gid_header: str | None = None,
) -> dict[str, Any] | None:
    subscription = svc.get_subscription(shop_domain)
    handle = str(redirect_plan_handle or "").strip().lower()
    if handle not in PLANS:
        handle = str((subscription or {}).get("plan_handle") or (subscription or {}).get("plan_name") or "").lower()
    if handle not in PLANS:
        return subscription

    shop_gid = str(shop_gid_header or (subscription or {}).get("shopify_shop_gid") or "").strip()
    verifier = svc._subscription_verifier
    configured = bool(verifier.app_gid and verifier.organization_id and verifier.access_token)
    if not configured or not shop_gid:
        if os.getenv("CREDIT_ENFORCEMENT_ENABLED", "false").lower() == "true":
            raise SubscriptionVerificationUnavailable(
                "Shopify Partner API billing verification is not configured"
            )
        return subscription

    verified = await verifier.verify(shop_gid)
    now = datetime.now(timezone.utc)
    if verified.get("status") != "active":
        if subscription:
            svc.upsert_subscription(
                shop_domain,
                {**subscription, "status": "cancelled", "last_verified_at": now.isoformat()},
            )
        return None
    cycle_start = (subscription or {}).get("current_period_start") or now.isoformat()
    cycle_end = (subscription or {}).get("current_period_end") or (now + timedelta(days=30)).isoformat()
    return svc.upsert_subscription(
        shop_domain,
        {
            **(subscription or {}),
            "status": "active",
            "plan_name": PLANS[handle].name,
            "plan_handle": handle,
            "shopify_shop_gid": shop_gid,
            "shopify_subscription_id": verified.get("shopify_subscription_id"),
            "current_period_start": cycle_start,
            "current_period_end": cycle_end,
            "last_verified_at": now.isoformat(),
            "overage_limit_cents": int((subscription or {}).get("overage_limit_cents") or 0),
        },
    )


class OverageLimitBody(BaseModel):
    overage_limit_cents: int = Field(ge=0, le=50_000)


async def require_paid_processing(
    request: Request, svc: SupabaseService, shop_domain: str
) -> BillingAccessContext:
    simulator = resolve_dev_billing_simulator_plan(request)
    if simulator:
        return BillingAccessContext("simulated", simulator)
    try:
        subscription = await _refresh_subscription(
            svc,
            shop_domain,
            shop_gid_header=request.headers.get("x-shopify-shop-gid"),
        )
    except SubscriptionVerificationUnavailable as exc:
        raise _billing_error(503, exc.code, str(exc)) from exc
    if not subscription or str(subscription.get("status") or "").lower() != "active":
        if os.getenv("CREDIT_ENFORCEMENT_ENABLED", "false").lower() != "true":
            # Instrumentation rollout mode records estimates without blocking
            # pre-launch/test merchants or delivering Shopify events.
            return BillingAccessContext("instrumentation", "starter")
        raise _billing_error(
            402,
            "SUBSCRIPTION_REQUIRED",
            "Choose a Shopify plan before starting paid processing",
        )
    handle = str(subscription.get("plan_handle") or "").lower()
    return BillingAccessContext("shopify", handle)


class LegacySubscriptionSyncBody(BaseModel):
    shop_domain: str
    subscription: dict[str, Any]


@router.post(
    "/billing/subscription/sync",
    summary="Deprecated internal subscription cache sync",
    deprecated=True,
)
async def sync_subscription_cache(
    request: Request,
    body: LegacySubscriptionSyncBody,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    """Retained for old internal callers; never treated as canonical verification."""
    tenant = require_authenticated_shop_domain(request, body.shop_domain)
    svc = _get_billing_svc(ctx)
    subscription = svc.upsert_subscription(tenant, body.subscription)
    svc.record_billing_event(tenant, "subscription_synced", body.subscription)
    return {"subscription": subscription}


@router.get("/billing/summary", summary="Get subscription and processing-credit summary")
async def get_billing_summary(
    request: Request,
    shop_domain: str | None = None,
    plan_handle: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_shop_domain(request, shop_domain)
    svc = _get_billing_svc(ctx)
    simulator_plan = resolve_dev_billing_simulator_plan(request)
    if simulator_plan:
        try:
            summary = svc.get_credit_summary(
                tenant, billing_mode="simulated", plan_handle=simulator_plan
            )
        except BillingStorageUnavailable as exc:
            raise _billing_error(503, exc.code, str(exc)) from exc
        return {"summary": {**summary, "hosted_plan_url": _hosted_plan_url(tenant)}}

    try:
        subscription = await _refresh_subscription(
            svc,
            tenant,
            redirect_plan_handle=plan_handle,
            shop_gid_header=request.headers.get("x-shopify-shop-gid"),
        )
    except SubscriptionVerificationUnavailable as exc:
        raise _billing_error(503, exc.code, str(exc)) from exc
    if not subscription or str(subscription.get("status") or "").lower() != "active":
        if os.getenv("CREDIT_ENFORCEMENT_ENABLED", "false").lower() != "true":
            try:
                summary = svc.get_credit_summary(
                    tenant, billing_mode="instrumentation", plan_handle="starter"
                )
            except BillingStorageUnavailable as exc:
                raise _billing_error(503, exc.code, str(exc)) from exc
            return {"summary": {**summary, "hosted_plan_url": _hosted_plan_url(tenant)}}
        return {"summary": _inactive_summary(tenant)}
    try:
        summary = svc.get_credit_summary(tenant, billing_mode="shopify")
    except BillingStorageUnavailable as exc:
        raise _billing_error(503, exc.code, str(exc)) from exc
    except Exception as exc:
        if "SUBSCRIPTION" in str(exc):
            return {"summary": _inactive_summary(tenant)}
        raise
    return {"summary": {**summary, "hosted_plan_url": _hosted_plan_url(tenant)}}


@router.put("/billing/overage-limit", summary="Set the monthly processing-credit overage limit")
async def update_overage_limit(
    request: Request,
    body: OverageLimitBody,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_shop_domain(request, shop_domain)
    svc = _get_billing_svc(ctx)
    simulator_plan = resolve_dev_billing_simulator_plan(request)
    instrumentation = (
        not simulator_plan
        and not svc.can_process(tenant)
        and os.getenv("CREDIT_ENFORCEMENT_ENABLED", "false").lower() != "true"
    )
    if not simulator_plan and not instrumentation and not svc.can_process(tenant):
        raise _billing_error(402, "SUBSCRIPTION_REQUIRED", "Choose a Shopify plan before enabling paid processing")
    try:
        effective_plan = simulator_plan if simulator_plan else ("starter" if instrumentation else None)
        svc.set_overage_limit(
            tenant,
            body.overage_limit_cents,
            billing_mode=("simulated" if simulator_plan else "instrumentation" if instrumentation else "shopify"),
            plan_handle=effective_plan,
        )
    except CreditLimitReached as exc:
        raise _billing_error(409, "CREDIT_LIMIT_REACHED", str(exc)) from exc
    except BillingStorageUnavailable as exc:
        raise _billing_error(503, exc.code, str(exc)) from exc
    mode = "simulated" if simulator_plan else "instrumentation" if instrumentation else "shopify"
    summary = svc.get_credit_summary(
        tenant, billing_mode=mode, plan_handle=effective_plan
    )
    return {"summary": {**summary, "hosted_plan_url": _hosted_plan_url(tenant)}}


@router.get("/billing/can-process", summary="Check paid-processing access")
async def can_process(
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_shop_domain(request, shop_domain)
    svc = _get_billing_svc(ctx)
    simulator = resolve_dev_billing_simulator_plan(request)
    return {"can_process": bool(simulator or svc.can_process(tenant))}


@router.post("/billing/events/flush", summary="Flush pending Shopify credit events")
async def flush_billing_events(
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, int]:
    tenant = require_authenticated_shop_domain(request, shop_domain)
    delivered = await drain_app_event_outbox(
        _get_billing_svc(ctx), ShopifyAppEventsClient(), shop_domain=tenant
    )
    return {"delivered": delivered}
