"""Shopify mandatory privacy webhook processing."""

import hmac
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app_context import AppContext, get_ctx
from .utils import require_shop_domain

router = APIRouter()

SHOP_TABLES = (
    "product_source_references",
    "product_intelligence_bulk_operations",
    "product_intelligence_suggestions",
    "product_intelligence_findings",
    "product_intelligence_audits",
    "submitted_documents",
    "product_drafts",
    "offload_jobs",
    "llm_model_configs",
    "usage_metrics",
    "billing_events",
    "merchant_subscriptions",
    "product_intelligence_normalization_settings",
    "shopify_sessions",
    "merchant_user_preferences",
)


def _require_service_key(request: Request) -> None:
    expected = os.getenv("INTERNAL_SERVICE_KEY", "").strip()
    supplied = request.headers.get("x-stockpile-service-key", "").strip()
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="Invalid service authentication")


@router.post("/compliance/{topic}", summary="Process a mandatory Shopify privacy webhook")
async def process_compliance_webhook(
    topic: str,
    request: Request,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    _require_service_key(request)
    shop_domain = require_shop_domain(request)
    normalized_topic = topic.strip().lower()
    if normalized_topic not in {"customers_data_request", "customers_redact", "shop_redact"}:
        raise HTTPException(status_code=404, detail="Unsupported compliance topic")

    # Stockpile does not ingest Shopify customer records. Customer-specific
    # topics are acknowledged without returning or deleting unrelated catalog data.
    if normalized_topic != "shop_redact":
        return {"ok": True, "topic": normalized_topic, "customer_data_stored": False}

    # The application context exposes the persistence adapter, while the
    # low-level Supabase client and storage bucket belong to its service.
    service = getattr(ctx.services.supabase, "_service", ctx.services.supabase)
    client = service._get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Persistence service unavailable")

    file_rows = (
        client.table("file_metadata")
        .select("storage_path,thumbnail_storage_path")
        .eq("shop_domain", shop_domain)
        .execute()
        .data
        or []
    )
    bucket = service._try_get_bucket()
    if bucket is not None:
        paths: list[str] = []
        for row in file_rows:
            if isinstance(row, dict):
                paths.extend(
                    str(value)
                    for value in (row.get("storage_path"), row.get("thumbnail_storage_path"))
                    if value
                )
        if paths:
            bucket.remove(paths)

    for table in SHOP_TABLES:
        try:
            client.table(table).delete().eq("shop_domain", shop_domain).execute()
        except Exception:
            # Older environments may not have every readiness table yet; the
            # file metadata deletion below remains mandatory.
            continue
    client.table("file_metadata").delete().eq("shop_domain", shop_domain).execute()
    return {"ok": True, "topic": normalized_topic, "shop_domain": shop_domain}
