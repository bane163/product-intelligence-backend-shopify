"""Shopify submission routes."""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse

from app_context import AppContext, get_ctx
from shared.metrics_signals import signal_api_error
from shared.observability import current_observability_fields
from shopify import (
    SHOPIFY_REAUTH_REQUIRED,
    ShopifyClient,
)  # kept for route-level monkeypatch compatibility in tests
from .utils import (
    parse_products_json,
    require_internal_service_key,
    resolve_shop_access_token,
    resolve_shop_domain,
)

router = APIRouter()
LOG = logging.getLogger(__name__)

_ACTIVE_SUBMIT_JOB_STATUSES = {"queued", "claimed", "running", "retryable"}
_REVIEW_REQUIRED_PLAN_STATUSES = {
    "failed",
    "partially_applied",
    "stale",
    "cancelled",
}


def _optional_str(entry: dict[str, Any] | None, key: str) -> str | None:
    if not isinstance(entry, dict):
        return None
    value = entry.get(key)
    return value if isinstance(value, str) else None


def _draft_products(entry: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(entry, dict):
        return []
    raw_products = entry.get("products")
    if not isinstance(raw_products, list):
        return []
    return [item for item in raw_products if isinstance(item, dict)]


def _save_submit_draft_state(
    *,
    ctx: AppContext,
    draft_id: str,
    shop_domain: str | None,
    fallback_run_id: str | None,
    fallback_name: str | None,
    submit_status: str | None,
    submit_run_id: str | None,
    submit_error: str | None,
) -> None:
    from application.use_cases.drafts.get_product_draft import (
        execute as get_draft_execute,
    )
    from application.use_cases.drafts.save_product_draft import (
        execute as save_draft_execute,
    )

    existing = get_draft_execute(
        supabase=ctx.supabase,
        draft_id=draft_id,
        shop_domain=shop_domain,
    )
    if not isinstance(existing, dict):
        return
    save_draft_execute(
        supabase=ctx.supabase,
        draft_id=draft_id,
        run_id=_optional_str(existing, "run_id") or fallback_run_id,
        import_mode=_optional_str(existing, "import_mode") or "auto",
        draft_name=_optional_str(existing, "draft_name") or fallback_name,
        shop_domain=_optional_str(existing, "shop_domain") or shop_domain,
        input_file_id=_optional_str(existing, "input_file_id"),
        input_filename=_optional_str(existing, "input_filename"),
        output_file_id=_optional_str(existing, "output_file_id"),
        output_filename=_optional_str(existing, "output_filename"),
        extraction_status=_optional_str(existing, "extraction_status"),
        extraction_run_id=_optional_str(existing, "extraction_run_id"),
        extraction_error=_optional_str(existing, "extraction_error"),
        submit_status=submit_status,
        submit_run_id=submit_run_id,
        submit_error=submit_error,
        require_lifecycle_columns=True,
        revision=int(existing.get("revision") or 1),
        products=_draft_products(existing),
    )


def _existing_submit_response(
    *, job: dict[str, Any], run_id: str, draft_id: str
) -> JSONResponse | None:
    status = str(job.get("status") or "").strip().lower()
    if status in _ACTIVE_SUBMIT_JOB_STATUSES:
        return JSONResponse(
            status_code=202,
            content={
                "run_id": run_id,
                "draft_id": draft_id,
                "submitted_id": job.get("submitted_id"),
                "status": status,
            },
        )
    if status == "succeeded":
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        return JSONResponse(
            status_code=200,
            content={
                **result,
                "run_id": run_id,
                "draft_id": draft_id,
                "submitted_id": result.get("submitted_id") or job.get("submitted_id"),
                "status": "succeeded",
            },
        )
    return None


@router.post("/submit-products", summary="Submit extracted products to Shopify")
async def submit_products_to_shopify(
    request: Request,
    products_json: str | None = Form(None),
    import_mode: str = Form("auto"),
    run_id: str | None = Form(None),
    draft_id: str | None = Form(None),
    submitted_id: str | None = Form(None),
    document_name: str | None = Form(None),
    shop_domain: str | None = Form(None),
    offload: bool = Form(False),
    plan_id: str | None = Form(None),
    revision: int | None = Form(None),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from application.use_cases.processing.submit_products import (
        execute as submit_execute,
    )
    from application.use_cases.drafts.save_product_draft import (
        execute as save_draft_execute,
    )
    from application.use_cases.submitted.get_submitted_document import (
        execute as get_submitted_execute,
    )

    require_internal_service_key(request)
    resolved_shop_domain = resolve_shop_domain(request, shop_domain)
    if not resolved_shop_domain:
        raise HTTPException(status_code=400, detail="Missing shop_domain")
    review_detail = {
        "code": "REVIEW_REQUIRED",
        "message": "An approved, current mutation plan is required",
    }
    if products_json or submitted_id or draft_id or not plan_id or revision is None:
        raise HTTPException(status_code=409, detail=review_detail)
    plan_store = getattr(ctx.supabase, "_service", ctx.supabase)
    plan = plan_store.get_mutation_plan(plan_id, shop_domain=resolved_shop_domain)
    if not isinstance(plan, dict):
        raise HTTPException(status_code=409, detail=review_detail)
    plan_status = str(plan.get("status") or "").strip().lower()
    if plan_status in _REVIEW_REQUIRED_PLAN_STATUSES:
        raise HTTPException(status_code=409, detail=review_detail)
    if plan_status not in {"approved", "executing", "completed"}:
        raise HTTPException(status_code=409, detail=review_detail)
    if int(plan.get("draft_revision") or 0) != int(revision):
        raise HTTPException(
            status_code=409,
            detail={**review_detail, "message": "Mutation plan revision is stale"},
        )
    resolved_draft_id = str(plan.get("draft_id") or "")
    current_draft = ctx.supabase.drafts.get_product_draft(
        resolved_draft_id, shop_domain=resolved_shop_domain
    )
    if not current_draft or int(current_draft.get("revision") or 1) != int(revision):
        raise HTTPException(
            status_code=409,
            detail={**review_detail, "message": "Draft changed after approval"},
        )
    selected_items = [
        x for x in plan.get("items", []) if isinstance(x, dict) and x.get("selected")
    ]
    if not selected_items or any(x.get("blockers") for x in selected_items):
        raise HTTPException(status_code=409, detail=review_detail)
    products_json = json.dumps([x.get("proposed") for x in selected_items])

    effective_run_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"shopify-mutation-plan:{plan_id}")
    )
    effective_job_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"shopify-mutation-job:{plan_id}")
    )
    existing_job = ctx.supabase.runs.get_offload_job(effective_job_id)
    if isinstance(existing_job, dict):
        existing_status = str(existing_job.get("status") or "").strip().lower()
        replay = _existing_submit_response(
            job=existing_job,
            run_id=effective_run_id,
            draft_id=resolved_draft_id,
        )
        if replay is not None:
            return replay
        if existing_status in {"failed", "cancelled"}:
            raise HTTPException(status_code=409, detail=review_detail)
    if plan_status in {"executing", "completed"}:
        # An active/completed plan without its deterministic reusable job is not
        # safe to execute again. The merchant must generate a fresh review.
        raise HTTPException(status_code=409, detail=review_detail)

    try:
        resolved_shop_access_token = __import__(
            "shopify_session_store"
        ).get_offline_access_token(resolved_shop_domain)
    except Exception as exc:
        raise HTTPException(
            status_code=401,
            detail={"code": SHOPIFY_REAUTH_REQUIRED, "action": "/auth/login"},
        ) from exc
    if not resolved_shop_access_token:
        raise HTTPException(
            status_code=401,
            detail={"code": SHOPIFY_REAUTH_REQUIRED, "action": "/auth/login"},
        )
    if offload:
        try:
            offline_token = __import__(
                "shopify_session_store"
            ).get_offline_access_token(resolved_shop_domain)
            if not offline_token:
                raise RuntimeError(SHOPIFY_REAUTH_REQUIRED)
        except Exception as exc:
            LOG.warning(
                "Rejecting offloaded submit without decryptable offline session shop=%s error=%s",
                resolved_shop_domain,
                exc,
            )
            raise HTTPException(status_code=401, detail=SHOPIFY_REAUTH_REQUIRED)
        observability_fields = current_observability_fields()
        try:
            enqueued_job = ctx.supabase.runs.enqueue_offload_job(
                effective_job_id,
                {
                    "queue_name": "offload",
                    "job_type": "shopify_submit",
                    "status": "queued",
                    "run_id": effective_run_id,
                    "draft_id": resolved_draft_id,
                    "submitted_id": submitted_id,
                    "shop_domain": resolved_shop_domain,
                    **observability_fields,
                    "payload": {
                        "import_mode": import_mode,
                        "document_name": document_name,
                        "plan_id": plan_id,
                        "revision": revision,
                    },
                },
                require_persistent_queue=True,
                create_once=True,
            )
            if not isinstance(enqueued_job, dict):
                raise RuntimeError("Offload queue did not return a submit job")
            _save_submit_draft_state(
                ctx=ctx,
                draft_id=resolved_draft_id,
                shop_domain=resolved_shop_domain,
                fallback_run_id=effective_run_id,
                fallback_name=document_name,
                submit_status="queued",
                submit_run_id=effective_run_id,
                submit_error=None,
            )
            ctx.supabase.runs.create_or_update_run(
                effective_run_id,
                {
                    "status": "queued",
                    "source": "shopify_submit",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "attempt": 1,
                    "shop_domain": resolved_shop_domain,
                    **observability_fields,
                },
            )
        except HTTPException:
            raise
        except Exception as exc:
            signal_api_error(
                route="/submit-products",
                method="POST",
                status_code=500,
                error=str(exc),
                offload=True,
            )
            raise HTTPException(status_code=500, detail=str(exc))
        return JSONResponse(
            status_code=202,
            content={
                "run_id": effective_run_id,
                "draft_id": resolved_draft_id,
                "submitted_id": submitted_id,
                "status": "queued",
            },
        )

    try:
        result = await submit_execute(
            supabase=ctx.supabase,
            shopify=ctx.services.shopify,
            tracing=ctx.services.tracing,
            products_json=products_json,
            import_mode=import_mode,
            run_id=run_id,
            draft_id=resolved_draft_id,
            submitted_id=submitted_id,
            document_name=document_name,
            shop_domain=resolved_shop_domain,
            shop_access_token=resolved_shop_access_token,
        )
        return result
    except RuntimeError as exc:
        if SHOPIFY_REAUTH_REQUIRED in str(exc):
            raise HTTPException(status_code=401, detail=SHOPIFY_REAUTH_REQUIRED)
        signal_api_error(
            route="/submit-products",
            method="POST",
            status_code=400,
            error=str(exc),
            offload=False,
        )
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        signal_api_error(
            route="/submit-products",
            method="POST",
            status_code=500,
            error=str(exc),
            offload=False,
        )
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/submit-status/{run_id}", summary="Poll submit job status")
async def get_submit_status(
    run_id: str,
    shop_domain: str | None = None,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    run = ctx.supabase.runs.get_run(run_id, shop_domain=shop_domain)
    if not isinstance(run, dict):
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "error": run.get("error"),
        "created_at": run.get("created_at"),
        "ended_at": run.get("ended_at"),
        "duration_ms": run.get("duration_ms"),
    }
