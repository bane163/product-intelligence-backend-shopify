import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable
from application.domain.product_intelligence_patching import VARIANT_OPERATIONS_FIELD
from application.ports.shopify_port import ShopifyPort
from application.ports.supabase_port import SupabaseNamespacedPort
from application.ports.tracing_port import TracingPort
from application.use_cases.intelligence_apply_suggestion import (
    _extract_user_errors,
    _normalize_variant_operations,
)

from api.agents.utils import normalize_shop_domain, parse_products_json
from shopify import ShopifyClient
from shared.observability import current_observability_fields

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class _SubmitSourceContext:
    products: list[dict[str, Any]]
    source: str
    tenant: str | None
    draft_id: str | None
    document_name: str | None
    submitted_document: dict[str, Any] | None
    draft_document: dict[str, Any] | None
    submitted_source_id: str | None
    draft_source_id: str | None


def _normalize_optional_string(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            return normalized
    return None


def _extract_document_products(
    document: dict[str, Any],
    *,
    invalid_payload_error: str,
    fail_submit: Callable[[str], None],
) -> list[dict[str, Any]]:
    stored_products = document.get("products")
    if not isinstance(stored_products, list):
        fail_submit(invalid_payload_error)
    return [item for item in stored_products if isinstance(item, dict)]


def _resolve_submit_source(
    *,
    supabase: SupabaseNamespacedPort,
    products_json: str | None,
    submitted_id: str | None,
    draft_id: str | None,
    document_name: str | None,
    shop_domain: str | None,
    fail_submit: Callable[[str], None],
) -> _SubmitSourceContext:
    tenant = normalize_shop_domain(shop_domain)
    submitted_source_id = _normalize_optional_string(submitted_id)
    draft_source_id = _normalize_optional_string(draft_id)
    resolved_draft_id = draft_source_id
    resolved_document_name = _normalize_optional_string(document_name)
    submitted_document: dict[str, Any] | None = None
    draft_document: dict[str, Any] | None = None
    products: list[dict[str, Any]] = []
    source = "products_json"

    if submitted_source_id:
        source = "submitted_document"
        submitted_document = supabase.submitted.get_submitted_document(
            submitted_source_id,
            shop_domain=tenant,
        )
        if not isinstance(submitted_document, dict):
            fail_submit("Submitted document not found")
        products = _extract_document_products(
            submitted_document,
            invalid_payload_error="Submitted document has invalid products payload",
            fail_submit=fail_submit,
        )
        if not resolved_draft_id:
            resolved_draft_id = _normalize_optional_string(
                submitted_document.get("draft_id")
            )
        if not resolved_document_name:
            resolved_document_name = _normalize_optional_string(
                submitted_document.get("name")
            )
        if not tenant:
            tenant = normalize_shop_domain(submitted_document.get("shop_domain"))
    elif draft_source_id:
        source = "draft"
        draft_document = supabase.drafts.get_product_draft(
            draft_source_id,
            shop_domain=tenant,
        )
        if not isinstance(draft_document, dict):
            fail_submit("Draft not found")
        products = _extract_document_products(
            draft_document,
            invalid_payload_error="Draft has invalid products payload",
            fail_submit=fail_submit,
        )
        resolved_draft_id = draft_source_id
        if not resolved_document_name:
            resolved_document_name = _normalize_optional_string(
                draft_document.get("draft_name")
                or draft_document.get("first_product_title")
            )
        if not tenant:
            tenant = normalize_shop_domain(draft_document.get("shop_domain"))
    elif isinstance(products_json, str) and products_json.strip():
        products = parse_products_json(products_json)

    if not products:
        fail_submit("No products provided")

    return _SubmitSourceContext(
        products=products,
        source=source,
        tenant=tenant,
        draft_id=resolved_draft_id,
        document_name=resolved_document_name,
        submitted_document=submitted_document,
        draft_document=draft_document,
        submitted_source_id=submitted_source_id,
        draft_source_id=draft_source_id,
    )


def _strip_internal_submit_fields(product: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(product)
    sanitized.pop(VARIANT_OPERATIONS_FIELD, None)
    return sanitized


def _collect_variant_operations_by_index(
    products: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    variant_operations_by_index: dict[int, list[dict[str, Any]]] = {}
    for product_index, product in enumerate(products):
        raw_operations = product.get(VARIANT_OPERATIONS_FIELD)
        candidate_operations = (
            [raw_operations]
            if isinstance(raw_operations, dict)
            else [item for item in raw_operations if isinstance(item, dict)]
            if isinstance(raw_operations, list)
            else []
        )
        normalized_operations: list[dict[str, Any]] = []
        for raw_operation in candidate_operations:
            normalized = _normalize_variant_operations(raw_operation)
            if normalized.get("create_options") or normalized.get("create_variants"):
                normalized_operations.append(normalized)
        if normalized_operations:
            variant_operations_by_index[product_index] = normalized_operations
    return variant_operations_by_index


async def _apply_variant_operations_for_product(
    *,
    shopify: ShopifyPort,
    product_id: str,
    operations: list[dict[str, Any]],
) -> None:
    for operation in operations:
        create_options = operation.get("create_options")
        if isinstance(create_options, list) and create_options:
            options_response = await shopify.create_product_options(
                product_id, create_options
            )
            option_errors = _extract_user_errors(
                options_response, ["data", "productOptionsCreate", "userErrors"]
            )
            if option_errors:
                raise RuntimeError(
                    f"AI enhancements failed while creating product options: {', '.join(option_errors)}"
                )
        create_variants = operation.get("create_variants")
        if isinstance(create_variants, list) and create_variants:
            variants_response = await shopify.bulk_create_product_variants(
                product_id, create_variants
            )
            variant_errors = _extract_user_errors(
                variants_response, ["data", "productVariantsBulkCreate", "userErrors"]
            )
            if variant_errors:
                raise RuntimeError(
                    f"AI enhancements failed while creating variants: {', '.join(variant_errors)}"
                )


async def _download_bulk_results(
    shopify: ShopifyPort, result_url: str
) -> list[dict[str, Any]]:
    """Download and parse the JSONL results from a completed bulk operation."""
    import httpx

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(result_url)
        resp.raise_for_status()
        text = resp.text.strip()
    if not text:
        return []
    results: list[dict[str, Any]] = []
    for line in text.split("\n"):
        line = line.strip()
        if line:
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return results


def _build_success_result_row(
    *,
    index: int,
    title: str,
    shopify_product_id: Any | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "index": index,
        "title": title,
        "status": "success",
        "mode": "productSet",
        "errors": [],
    }
    if shopify_product_id is not None:
        row["shopify_product_id"] = shopify_product_id
    return row


def _build_failed_result_row(
    *,
    index: int,
    title: str,
    message: str | None = None,
    errors: list[Any] | None = None,
    shopify_product_id: Any | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "index": index,
        "title": title,
        "status": "failed",
        "errors": errors
        if isinstance(errors, list) and errors
        else [{"field": None, "message": message or "Unknown submit error"}],
    }
    if shopify_product_id is not None:
        row["shopify_product_id"] = shopify_product_id
    return row


async def execute(
    supabase: SupabaseNamespacedPort,
    shopify: ShopifyPort,
    tracing: TracingPort,
    products_json: str | None = None,
    import_mode: str = "auto",
    run_id: str | None = None,
    draft_id: str | None = None,
    submitted_id: str | None = None,
    document_name: str | None = None,
    shop_domain: str | None = None,
    shop_access_token: str | None = None,
) -> dict[str, object]:
    submit_started_at = perf_counter()
    current_run_id = run_id or str(uuid.uuid4())
    observability_fields = current_observability_fields()
    emitter = None
    try:
        from application.services.run_event_emitter import RunEventEmitter

        emitter = RunEventEmitter(
            tracing=tracing, supabase=supabase, run_id=current_run_id
        )
        emit_and_persist = emitter.emit_and_persist
    except Exception:

        def emit_and_persist(*args, **kwargs):
            return None

    supabase.runs.create_or_update_run(
        current_run_id,
        {
            "status": "queued",
            "source": "shopify_submit",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "attempt": 1,
            "shop_domain": shop_domain,
            **observability_fields,
        },
    )
    emit_and_persist(phase="submit_start", message="Starting Shopify submit")
    supabase.runs.create_or_update_run(
        current_run_id,
        {
            "status": "running",
            "shop_domain": shop_domain,
        },
    )
    def fail_submit(message: str) -> None:
        emit_and_persist(
            phase="workflow_error",
            message=message,
            level="error",
            error=message,
        )
        try:
            supabase.runs.finalize_run(current_run_id, status="error", error=message)
        except Exception:
            pass
        try:
            tracing.complete_run(current_run_id)
        except Exception:
            pass
        raise RuntimeError(message)

    source_context = _resolve_submit_source(
        supabase=supabase,
        products_json=products_json,
        submitted_id=submitted_id,
        draft_id=draft_id,
        document_name=document_name,
        shop_domain=shop_domain,
        fail_submit=fail_submit,
    )
    products = source_context.products
    tenant = source_context.tenant
    draft_id = source_context.draft_id
    document_name = source_context.document_name
    submitted_source_id = source_context.submitted_source_id
    draft_source_id = source_context.draft_source_id

    submit_client = shopify
    # Use request-scoped credentials when provided, and also when tenant can be
    # inferred from the source draft/submitted document.
    if tenant or (isinstance(shop_access_token, str) and shop_access_token.strip()):
        submit_client = ShopifyClient(
            shop=tenant,
            token=shop_access_token.strip() if isinstance(shop_access_token, str) else None,
        )

    variant_operations_by_index = _collect_variant_operations_by_index(products)
    submit_products = [_strip_internal_submit_fields(item) for item in products]
    source = source_context.source
    client_type = type(submit_client).__name__
    client_shop = tenant
    if not client_shop:
        inner_client = getattr(submit_client, "_client", None)
        client_shop = getattr(inner_client, "shop", None)
    emit_and_persist(
        phase="submit_products_loaded",
        message="Loaded products for submit",
        payload_preview={
            "count": len(submit_products),
            "mode": import_mode,
            "shop_domain": tenant,
            "has_token": bool(shop_access_token),
            "variant_operations_count": sum(
                len(items) for items in variant_operations_by_index.values()
            ),
            "source": source,
        },
    )
    LOG.info(
        "submit_start run_id=%s draft_id=%s submitted_id=%s tenant=%s source=%s "
        "products=%s has_token=%s client=%s client_shop=%s",
        current_run_id,
        draft_id,
        submitted_id,
        tenant,
        source,
        len(submit_products),
        bool(shop_access_token),
        client_type,
        client_shop,
    )

    results: list[dict[str, Any]] = []
    # ── Bulk operation flow via productSet ──────────────────────────────
    try:
        emit_and_persist(
            phase="submit_bulk_build",
            message=f"Building JSONL for {len(submit_products)} products",
        )
        jsonl_data = submit_client.build_product_set_jsonl(submit_products)

        emit_and_persist(
            phase="submit_bulk_upload",
            message="Creating staged upload target",
        )
        LOG.info(
            "submit_bulk_create_staged_upload run_id=%s tenant=%s products=%s",
            current_run_id,
            tenant,
            len(submit_products),
        )
        staged_target = await submit_client.create_staged_upload()
        upload_url = staged_target.get("url", "")
        upload_params = staged_target.get("parameters", [])
        resource_url = staged_target.get("resourceUrl", "")

        emit_and_persist(
            phase="submit_bulk_upload",
            message="Uploading JSONL to staged target",
        )
        LOG.info(
            "submit_bulk_upload_jsonl run_id=%s has_upload_url=%s params=%s",
            current_run_id,
            bool(upload_url),
            len(upload_params),
        )
        await submit_client.upload_to_staged_url(upload_url, upload_params, jsonl_data)

        # The stagedUploadPath is the key from the resourceUrl
        staged_path = resource_url
        # Some Shopify staged uploads return the path in a parameter named "key"
        for param in upload_params:
            if param.get("name") == "key":
                staged_path = param.get("value", resource_url)
                break

        emit_and_persist(
            phase="submit_bulk_start",
            message="Starting bulk mutation operation",
        )
        LOG.info(
            "submit_bulk_start_mutation run_id=%s has_staged_path=%s",
            current_run_id,
            bool(staged_path),
        )
        bulk_op = await submit_client.run_bulk_mutation(staged_path)
        operation_id = bulk_op.get("id", "")

        emit_and_persist(
            phase="submit_bulk_polling",
            message=f"Polling bulk operation {operation_id}",
            payload_preview={"operation_id": operation_id},
        )
        LOG.info(
            "submit_bulk_polling run_id=%s operation_id=%s",
            current_run_id,
            operation_id,
        )
        final_status = await submit_client.wait_for_bulk_operation(
            operation_id, poll_interval=3.0, timeout=600.0
        )

        bulk_status = final_status.get("status", "UNKNOWN")
        result_url = final_status.get("url")
        root_count = final_status.get("rootObjectCount", 0)

        emit_and_persist(
            phase="submit_bulk_complete",
            message=f"Bulk operation {bulk_status}: {root_count} root objects",
            payload_preview={
                "operation_id": operation_id,
                "status": bulk_status,
                "rootObjectCount": root_count,
                "objectCount": final_status.get("objectCount", 0),
            },
        )
        LOG.info(
            "submit_bulk_complete run_id=%s operation_id=%s status=%s root_count=%s object_count=%s",
            current_run_id,
            operation_id,
            bulk_status,
            root_count,
            final_status.get("objectCount", 0),
        )

        if bulk_status == "COMPLETED" and result_url:
            # Download and parse result JSONL
            bulk_results = await _download_bulk_results(submit_client, result_url)
            for index, product in enumerate(submit_products):
                title = product.get("title", f"Product {index}")
                if index < len(bulk_results):
                    result_data = bulk_results[index]
                    product_set = result_data.get("data", {}).get("productSet", {})
                    user_errors = product_set.get("userErrors", [])
                    product_data = product_set.get("product", {})
                    if user_errors:
                        results.append(
                            _build_failed_result_row(
                                index=index,
                                title=title,
                                errors=user_errors,
                                shopify_product_id=product_data.get("id"),
                            )
                        )
                    else:
                        created_id = product_data.get("id", "")
                        # Apply variant operations if any
                        variant_ops = variant_operations_by_index.get(index, [])
                        if variant_ops and created_id:
                            try:
                                await _apply_variant_operations_for_product(
                                    shopify=submit_client,
                                    product_id=created_id,
                                    operations=variant_ops,
                                )
                            except Exception as var_exc:
                                emit_and_persist(
                                    phase="submit_item_warning",
                                    message=f"Variant operations failed for '{title}'",
                                    level="warning",
                                    error=str(var_exc),
                                )
                        results.append(
                            _build_success_result_row(
                                index=index,
                                title=product_data.get("title", title),
                                shopify_product_id=created_id,
                            )
                        )
                else:
                    results.append(
                        _build_failed_result_row(
                            index=index,
                            title=title,
                            message="No result from bulk operation",
                        )
                    )
        elif bulk_status == "COMPLETED":
            # Completed but no result URL — assume all succeeded
            for index, product in enumerate(submit_products):
                results.append(
                    _build_success_result_row(
                        index=index,
                        title=product.get("title", f"Product {index}"),
                    )
                )
        else:
            error_code = final_status.get("errorCode", "UNKNOWN")
            for index, product in enumerate(submit_products):
                results.append(
                    _build_failed_result_row(
                        index=index,
                        title=product.get("title", f"Product {index}"),
                        message=f"Bulk operation {bulk_status}: {error_code}",
                    )
                )
    except Exception as exc:
        LOG.exception(
            "submit_bulk_exception run_id=%s draft_id=%s tenant=%s",
            current_run_id,
            draft_id,
            tenant,
        )
        emit_and_persist(
            phase="submit_bulk_error",
            message=f"Bulk submit failed: {exc}",
            level="error",
            error=str(exc),
        )
        for index, product in enumerate(submit_products):
            results.append(
                _build_failed_result_row(
                    index=index,
                    title=product.get("title", f"Product {index}"),
                    message=str(exc),
                )
            )

    successful_indexes: list[int] = []
    seen_indexes: set[int] = set()
    for row in results:
        if row.get("status") != "success":
            continue
        idx = row.get("index")
        if (
            isinstance(idx, int)
            and 0 <= idx < len(submit_products)
            and idx not in seen_indexes
        ):
            seen_indexes.add(idx)
            successful_indexes.append(idx)
    success_count = len(successful_indexes)
    failed_count = max(0, len(submit_products) - success_count)
    status = "success" if success_count > 0 else "error"
    submitted_id: str | None = None
    warnings: list[str] = []
    # Collect the first error message for propagation to the caller
    error_message: str | None = None
    if status == "error":
        for r in results:
            if r.get("status") != "failed":
                continue
            row_errors = r.get("errors")
            if not isinstance(row_errors, list) or not row_errors:
                continue
            first_error = row_errors[0]
            if isinstance(first_error, dict):
                message = first_error.get("message")
                if isinstance(message, str) and message.strip():
                    error_message = message.strip()
                    break
                error_message = json.dumps(first_error, ensure_ascii=False)
                break
            if isinstance(first_error, str) and first_error.strip():
                error_message = first_error.strip()
                break
        if not error_message:
            error_message = (
                f"No products were submitted successfully "
                f"(success_count=0, failed_count={failed_count})"
            )
    elif failed_count:
        warnings.append(
            f"Submitted {success_count} of {len(submit_products)} products; {failed_count} failed"
        )
    if status == "success":
        inferred_name = document_name
        if not inferred_name and draft_id:
            draft = supabase.drafts.get_product_draft(draft_id, shop_domain=tenant)
            if draft:
                inferred_name = draft.get("draft_name") or draft.get(
                    "first_product_title"
                )
        if not inferred_name:
            inferred_name = str(submit_products[0].get("title") or "Submitted document")
        submitted_products = [submit_products[idx] for idx in successful_indexes]
        submitted = supabase.submitted.save_submitted_document(
            submitted_id=str(uuid.uuid4()),
            run_id=current_run_id,
            draft_id=draft_id,
            name=str(inferred_name),
            import_mode="auto",
            shop_domain=tenant,
            product_count=len(submitted_products),
            products=submitted_products,
        )
        submitted_id = str(submitted.get("submitted_id"))

    submit_duration_ms = int((perf_counter() - submit_started_at) * 1000)
    supabase.runs.finalize_run(
        current_run_id,
        status=status,
        duration_ms=submit_duration_ms,
        error=error_message if status != "success" else None,
    )
    tracing.complete_run(current_run_id)
    LOG.info(
        "submit_complete run_id=%s status=%s success_count=%s failed_count=%s submitted_id=%s error=%s warnings=%s",
        current_run_id,
        status,
        success_count,
        failed_count,
        submitted_id,
        error_message,
        len(warnings),
    )

    return {
        "success_count": success_count,
        "failed_count": failed_count,
        "results": results,
        "submitted_id": submitted_id,
        "duration_ms": submit_duration_ms,
        "warnings": warnings,
        "error": error_message,
    }
