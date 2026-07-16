"""Billing and subscription routes."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app_context import AppContext, get_ctx
from services.supabase_service import SupabaseService

from .utils import (
    require_authenticated_shop_domain,
    require_shop_domain,
    resolve_dev_billing_simulator_plan,
)

LOG = logging.getLogger(__name__)

router = APIRouter()


def _empty_usage_payload() -> dict[str, int]:
    return {
        "files_processed": 0,
        "files_included": 0,
        "overage_files": 0,
        "tokens_used": 0,
    }


class UsageIncrementBody(BaseModel):
    files: int = 1
    tokens: int = 0


class SubscriptionSyncBody(BaseModel):
    shop_domain: str
    subscription: dict[str, Any]


def _get_billing_svc(ctx: AppContext) -> SupabaseService:
    """Access the underlying SupabaseService which has billing mixin methods."""
    svc = getattr(ctx.supabase, "_svc", None) or getattr(
        ctx.supabase, "_service", None
    )
    if svc and isinstance(svc, SupabaseService):
        return svc
    return SupabaseService()


@router.get("/billing/subscription", summary="Get merchant subscription")
async def get_subscription(
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_shop_domain(request, shop_domain)
    svc = _get_billing_svc(ctx)
    sub = svc.get_subscription(tenant)
    if not sub:
        raise HTTPException(status_code=404, detail="No subscription found")
    return {"subscription": sub}


@router.get("/billing/usage", summary="Get current cycle usage metrics")
async def get_usage(
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_shop_domain(request, shop_domain)
    svc = _get_billing_svc(ctx)
    usage = svc.get_current_usage(tenant)
    if not usage:
        return {
            "usage": {
                "files_processed": 0,
                "files_included": 0,
                "overage_files": 0,
                "tokens_used": 0,
            }
        }
    return {"usage": usage}


@router.post("/billing/usage/increment", summary="Increment file usage count")
async def increment_usage(
    request: Request,
    body: UsageIncrementBody,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_shop_domain(request, shop_domain)
    svc = _get_billing_svc(ctx)
    if resolve_dev_billing_simulator_plan(request) is not None:
        return {"usage": svc.get_current_usage(tenant) or _empty_usage_payload()}
    if not svc.can_process(tenant):
        raise HTTPException(
            status_code=402, detail="Subscription required to process files"
        )
    updated = svc.increment_usage(tenant, files=body.files, tokens=body.tokens)
    return {"usage": updated}


@router.post("/billing/subscription/sync", summary="Sync subscription from webhook")
async def sync_subscription(
    request: Request,
    body: SubscriptionSyncBody,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_authenticated_shop_domain(request, body.shop_domain)
    svc = _get_billing_svc(ctx)
    sub = svc.upsert_subscription(tenant, body.subscription)
    svc.record_billing_event(tenant, "subscription_synced", body.subscription)
    return {"subscription": sub}


@router.get("/billing/can-process", summary="Check if merchant can process files")
async def can_process(
    request: Request,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_shop_domain(request, shop_domain)
    svc = _get_billing_svc(ctx)
    allowed = resolve_dev_billing_simulator_plan(request) is not None or svc.can_process(
        tenant
    )
    return {"can_process": allowed}
