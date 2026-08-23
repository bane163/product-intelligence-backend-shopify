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
from application.services.product_media_ingestion import (
    PRODUCT_MEDIA_FILE_ORIGIN,
    fetch_external_image,
)

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

    # An approved mutation plan is passed to this use case as products_json while
    # draft_id remains attached for tenancy, lifecycle, and document metadata.
    # Parse that authoritative payload before loading either metadata source so a
    # draft can never silently replace the merchant's selected plan items.
    has_products_json = isinstance(products_json, str) and bool(products_json.strip())
    if has_products_json:
        products = parse_products_json(products_json)

    if submitted_source_id:
        source = "submitted_document"
        submitted_document = supabase.submitted.get_submitted_document(
            submitted_source_id,
            shop_domain=tenant,
        )
        if not isinstance(submitted_document, dict):
            fail_submit("Submitted document not found")
        if not has_products_json:
            products = _extract_document_products(
                submitted_document,
                invalid_payload_error="Submitted document has invalid products payload",
                fail_submit=fail_submit,
            )
        else:
            source = "products_json_with_submitted_metadata"
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
        if not has_products_json:
            products = _extract_document_products(
                draft_document,
                invalid_payload_error="Draft has invalid products payload",
                fail_submit=fail_submit,
            )
        else:
            source = "products_json_with_draft_metadata"
        resolved_draft_id = draft_source_id
        if not resolved_document_name:
            resolved_document_name = _normalize_optional_string(
                draft_document.get("draft_name")
                or draft_document.get("first_product_title")
            )
        if not tenant:
            tenant = normalize_shop_domain(draft_document.get("shop_domain"))
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
    sanitized.pop("_approved_media", None)
    sanitized.pop("media_gallery", None)
    return sanitized


async def _prepare_product_media(
    *,
    supabase: SupabaseNamespacedPort,
    shopify: ShopifyPort,
    product: dict[str, Any],
    shop_domain: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Stage approved image bytes and return a Shopify-safe product payload."""

    raw_gallery = product.get("_approved_media")
    gallery = (
        [item for item in raw_gallery if isinstance(item, dict)]
        if isinstance(raw_gallery, list)
        else []
    )
    existing_ids = [
        str(item["shopify_media_id"])
        for item in gallery
        if item.get("source_kind") == "shopify_existing"
        and item.get("shopify_media_id")
    ]
    incoming = [
        item for item in gallery if item.get("source_kind") != "shopify_existing"
    ]
    staged_files: list[dict[str, str]] = []
    for item in incoming:
        asset_id = _normalize_optional_string(item.get("asset_id"))
        content: bytes
        mime_type: str
        filename: str
        if asset_id:
            stored = supabase.file.get_file(asset_id)
            if (
                not isinstance(stored, dict)
                or str(stored.get("shop_domain") or "").strip().lower() != shop_domain
                or stored.get("file_origin") != PRODUCT_MEDIA_FILE_ORIGIN
                or not isinstance(stored.get("content"), (bytes, bytearray))
            ):
                raise RuntimeError("An approved image is unavailable for this shop")
            content = bytes(stored["content"])
            mime_type = str(
                stored.get("content_type") or item.get("content_type") or ""
            )
            filename = str(stored.get("name") or asset_id)
            expected_hash = _normalize_optional_string(item.get("content_sha256"))
            if expected_hash:
                import hashlib

                if hashlib.sha256(content).hexdigest() != expected_hash:
                    raise RuntimeError("An approved image changed after review")
        else:
            source_url = _normalize_optional_string(item.get("source_url"))
            if not source_url:
                raise RuntimeError("An approved image has no stored bytes")
            content, mime_type, extension, _width, _height, _resolved = (
                await fetch_external_image(source_url)
            )
            filename = f"product-image.{extension}"
        if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise RuntimeError("An approved image has an unsupported content type")
        target = await shopify.create_staged_image_upload(
            filename=filename,
            mime_type=mime_type,
            file_size=len(content),
        )
        resource_url = str(target.get("resourceUrl") or "").strip()
        upload_url = str(target.get("url") or "").strip()
        if not resource_url or not upload_url:
            raise RuntimeError("Shopify did not return a usable image upload target")
        await shopify.upload_bytes_to_staged_url(
            url=upload_url,
            parameters=(
                target.get("parameters")
                if isinstance(target.get("parameters"), list)
                else []
            ),
            content=content,
            filename=filename,
            mime_type=mime_type,
            http_method=str(target.get("httpMethod") or "POST"),
        )
        file_input = {"contentType": "IMAGE", "originalSource": resource_url}
        alt_text = _normalize_optional_string(item.get("alt_text"))
        if alt_text:
            file_input["alt"] = alt_text
        staged_files.append(file_input)
    prepared = _strip_internal_submit_fields(product)
    if staged_files:
        prepared["files"] = [
            *({"id": media_id} for media_id in existing_ids),
            *staged_files,
        ]
    return prepared, {
        "existing_media_ids": existing_ids,
        "expected_new_count": len(staged_files),
    }


def _collect_variant_operations_by_index(
    products: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    variant_operations_by_index: dict[int, list[dict[str, Any]]] = {}
    for product_index, product in enumerate(products):
        raw_operations = product.get(VARIANT_OPERATIONS_FIELD)
        candidate_operations = (
            [raw_operations]
            if isinstance(raw_operations, dict)
            else (
                [item for item in raw_operations if isinstance(item, dict)]
                if isinstance(raw_operations, list)
                else []
            )
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
        "errors": (
            errors
            if isinstance(errors, list) and errors
            else [{"field": None, "message": message or "Unknown submit error"}]
        ),
    }
    if shopify_product_id is not None:
        row["shopify_product_id"] = shopify_product_id
    return row


def _build_partially_applied_result_row(
    *,
    index: int,
    title: str,
    shopify_product_id: str,
    errors: list[Any],
    failed_component: str = "metafields",
) -> dict[str, Any]:
    return {
        "index": index,
        "title": title,
        "status": "partially_applied",
        "mode": "productSet",
        "shopify_product_id": shopify_product_id,
        "errors": errors,
        "applied": ["product"],
        "failed": [failed_component],
    }


def _media_processing_errors(status: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for media in (
        status.get("failed", []) if isinstance(status.get("failed"), list) else []
    ):
        if not isinstance(media, dict):
            continue
        raw_errors = [
            *(
                media.get("fileErrors")
                if isinstance(media.get("fileErrors"), list)
                else []
            ),
            *(
                media.get("mediaErrors")
                if isinstance(media.get("mediaErrors"), list)
                else []
            ),
        ]
        if not raw_errors:
            raw_errors = [{"message": "Shopify failed to process this image."}]
        for error in raw_errors:
            errors.append(
                {
                    "field": ["media"],
                    "code": error.get("code") if isinstance(error, dict) else None,
                    "message": (
                        str(
                            error.get("message")
                            or "Shopify failed to process this image."
                        )
                        if isinstance(error, dict)
                        else str(error)
                    ),
                }
            )
    if status.get("status") == "timeout":
        errors.append(
            {
                "field": ["media"],
                "code": "MEDIA_PROCESSING_TIMEOUT",
                "message": "The product was applied, but Shopify did not finish processing its images in time.",
            }
        )
    return errors


def _malformed_bulk_result_message(detail: str) -> str:
    return f"Malformed Shopify bulk result: {detail}"


def _parse_bulk_product_set_result(
    *, result_data: Any, index: int, title: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return an item result row and product data when Shopify returned it."""
    if not isinstance(result_data, dict):
        return (
            _build_failed_result_row(
                index=index,
                title=title,
                message=_malformed_bulk_result_message("root row is not an object"),
            ),
            None,
        )
    data = result_data.get("data")
    if not isinstance(data, dict):
        return (
            _build_failed_result_row(
                index=index,
                title=title,
                message=_malformed_bulk_result_message("missing data object"),
            ),
            None,
        )
    product_set = data.get("productSet")
    if not isinstance(product_set, dict):
        return (
            _build_failed_result_row(
                index=index,
                title=title,
                message=_malformed_bulk_result_message("missing productSet object"),
            ),
            None,
        )
    raw_errors = product_set.get("userErrors")
    user_errors = raw_errors if isinstance(raw_errors, list) else []
    product_data = product_set.get("product")
    if user_errors:
        return (
            _build_failed_result_row(
                index=index,
                title=title,
                errors=user_errors,
                shopify_product_id=(
                    product_data.get("id") if isinstance(product_data, dict) else None
                ),
            ),
            product_data if isinstance(product_data, dict) else None,
        )
    if not isinstance(product_data, dict) or not product_data:
        detail = (
            "userErrors is not an array"
            if raw_errors is not None and not isinstance(raw_errors, list)
            else "neither product data nor user errors were returned"
        )
        return (
            _build_failed_result_row(
                index=index,
                title=title,
                message=_malformed_bulk_result_message(detail),
            ),
            None,
        )
    return (
        _build_success_result_row(
            index=index,
            title=str(product_data.get("title") or title),
            shopify_product_id=product_data.get("id"),
        ),
        product_data,
    )


def _metafield_inputs(product: dict[str, Any], owner_id: str) -> list[dict[str, str]]:
    raw = product.get("metafields")
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        namespace = _normalize_optional_string(item.get("namespace"))
        key = _normalize_optional_string(item.get("key"))
        value = item.get("value")
        mf_type = (
            _normalize_optional_string(item.get("type")) or "single_line_text_field"
        )
        if namespace and key and isinstance(value, str):
            result.append(
                {
                    "ownerId": owner_id,
                    "namespace": namespace,
                    "key": key,
                    "value": value,
                    "type": mf_type,
                }
            )
    return result


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
    ai_enhancements_enabled: bool = True,
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
            "ai_enhancements_enabled": bool(ai_enhancements_enabled),
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
            token=(
                shop_access_token.strip()
                if isinstance(shop_access_token, str)
                else None
            ),
        )

    variant_operations_by_index = _collect_variant_operations_by_index(products)
    results: list[dict[str, Any]] = []
    submit_products: list[dict[str, Any]] = []
    submit_index_map: list[int] = []
    prepared_products_by_index: dict[int, dict[str, Any]] = {}
    media_expectations_by_index: dict[int, dict[str, Any]] = {}
    for original_index, product in enumerate(products):
        title = str(product.get("title") or f"Product {original_index}")
        try:
            prepared, media_expectation = await _prepare_product_media(
                supabase=supabase,
                shopify=submit_client,
                product=product,
                shop_domain=tenant or "",
            )
        except Exception as media_exc:
            emit_and_persist(
                phase="submit_item_error",
                message=f"Could not prepare images for '{title}'",
                level="error",
                error=str(media_exc),
            )
            results.append(
                _build_failed_result_row(
                    index=original_index,
                    title=title,
                    message=f"Image upload preparation failed: {media_exc}",
                )
            )
            continue
        submit_products.append(prepared)
        submit_index_map.append(original_index)
        prepared_products_by_index[original_index] = prepared
        media_expectations_by_index[original_index] = media_expectation
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

    # ── Bulk operation flow via productSet ──────────────────────────────
    try:
        if not submit_products:
            raise RuntimeError("No products passed image upload preparation")
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
        result_url = final_status.get("url") or final_status.get("partialDataUrl")
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
            results_by_line: dict[int, Any] = {}
            unnumbered_results: list[Any] = []
            for row in bulk_results:
                if (
                    isinstance(row, dict)
                    and isinstance(row.get("__lineNumber"), (int, str))
                    and str(row.get("__lineNumber")).isdigit()
                ):
                    results_by_line[int(row["__lineNumber"])] = row
                else:
                    unnumbered_results.append(row)
            missing_result = object()
            for line_index, product in enumerate(submit_products):
                original_index = submit_index_map[line_index]
                title = product.get("title", f"Product {original_index}")
                result_data = results_by_line.get(line_index, missing_result)
                if (
                    result_data is missing_result
                    and not results_by_line
                    and line_index < len(unnumbered_results)
                ):
                    result_data = unnumbered_results[line_index]
                if result_data is not missing_result:
                    parsed_row, product_data = _parse_bulk_product_set_result(
                        result_data=result_data,
                        index=original_index,
                        title=title,
                    )
                    if parsed_row.get("status") == "success" and product_data:
                        created_id = product_data.get("id", "")
                        # Apply variant operations if any
                        variant_ops = variant_operations_by_index.get(
                            original_index, []
                        )
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
                                    _build_failed_result_row(
                                        index=original_index,
                                        title=title,
                                        message=f"Product partially applied; variant step failed: {var_exc}",
                                        shopify_product_id=created_id,
                                    )
                                )
                                continue
                        metafields = (
                            _metafield_inputs(product, created_id) if created_id else []
                        )
                        if metafields:
                            try:
                                metafields_response = (
                                    await submit_client.set_product_metafields(
                                        created_id, metafields
                                    )
                                )
                                metafield_errors = _extract_user_errors(
                                    metafields_response,
                                    ["data", "metafieldsSet", "userErrors"],
                                )
                                if metafield_errors:
                                    results.append(
                                        _build_partially_applied_result_row(
                                            index=original_index,
                                            title=product_data.get("title", title),
                                            shopify_product_id=created_id,
                                            errors=[
                                                {
                                                    "field": ["metafields"],
                                                    "message": message,
                                                }
                                                for message in metafield_errors
                                            ],
                                        )
                                    )
                                    continue
                            except Exception as metafield_exc:
                                results.append(
                                    _build_partially_applied_result_row(
                                        index=original_index,
                                        title=product_data.get("title", title),
                                        shopify_product_id=created_id,
                                        errors=[
                                            {
                                                "field": ["metafields"],
                                                "message": str(metafield_exc),
                                            }
                                        ],
                                    )
                                )
                                continue
                        media_expectation = media_expectations_by_index.get(
                            original_index, {}
                        )
                        expected_new_count = int(
                            media_expectation.get("expected_new_count") or 0
                        )
                        if expected_new_count and created_id:
                            try:
                                media_status = (
                                    await submit_client.wait_for_product_media(
                                        created_id,
                                        existing_media_ids=list(
                                            media_expectation.get("existing_media_ids")
                                            or []
                                        ),
                                        expected_new_count=expected_new_count,
                                    )
                                )
                            except Exception as media_exc:
                                media_status = {
                                    "status": "failed",
                                    "failed": [
                                        {
                                            "mediaErrors": [
                                                {
                                                    "code": "MEDIA_STATUS_CHECK_FAILED",
                                                    "message": str(media_exc),
                                                }
                                            ]
                                        }
                                    ],
                                }
                            if media_status.get("status") != "ready":
                                media_errors = _media_processing_errors(media_status)
                                if not media_errors:
                                    media_errors = [
                                        {
                                            "field": ["media"],
                                            "code": "MEDIA_PROCESSING_FAILED",
                                            "message": "Shopify did not confirm the approved product images.",
                                        }
                                    ]
                                results.append(
                                    _build_partially_applied_result_row(
                                        index=original_index,
                                        title=product_data.get("title", title),
                                        shopify_product_id=created_id,
                                        errors=media_errors,
                                        failed_component="media",
                                    )
                                )
                                continue
                        results.append(
                            _build_success_result_row(
                                index=original_index,
                                title=product_data.get("title", title),
                                shopify_product_id=created_id,
                            )
                        )
                    else:
                        results.append(parsed_row)
                else:
                    results.append(
                        _build_failed_result_row(
                            index=original_index,
                            title=title,
                            message="No result from bulk operation",
                        )
                    )
        elif bulk_status == "COMPLETED":
            # Shopify completion without output is not evidence of item success.
            for line_index, product in enumerate(submit_products):
                original_index = submit_index_map[line_index]
                results.append(
                    _build_failed_result_row(
                        index=original_index,
                        title=product.get("title", f"Product {original_index}"),
                        message="Bulk operation completed without a result URL",
                    )
                )
        else:
            error_code = final_status.get("errorCode", "UNKNOWN")
            for line_index, product in enumerate(submit_products):
                original_index = submit_index_map[line_index]
                results.append(
                    _build_failed_result_row(
                        index=original_index,
                        title=product.get("title", f"Product {original_index}"),
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
        for line_index, product in enumerate(submit_products):
            original_index = submit_index_map[line_index]
            results.append(
                _build_failed_result_row(
                    index=original_index,
                    title=product.get("title", f"Product {original_index}"),
                    message=str(exc),
                )
            )

    successful_indexes: list[int] = []
    seen_indexes: set[int] = set()
    for row in results:
        if row.get("status") not in {"success", "partially_applied"}:
            continue
        idx = row.get("index")
        if (
            isinstance(idx, int)
            and 0 <= idx < len(products)
            and idx not in seen_indexes
        ):
            seen_indexes.add(idx)
            successful_indexes.append(idx)
    success_count = len(successful_indexes)
    failed_count = max(0, len(products) - success_count)
    partial_count = sum(
        1 for row in results if row.get("status") == "partially_applied"
    )
    fully_applied = (
        success_count == len(products) and failed_count == 0 and partial_count == 0
    )
    status = "success" if fully_applied else "error"
    submitted_id: str | None = None
    warnings: list[str] = []
    # Collect the first error message for propagation to the caller
    error_message: str | None = None
    if status == "error":
        for r in results:
            if r.get("status") not in {"failed", "partially_applied"}:
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
            f"Submitted {success_count} of {len(products)} products; {failed_count} failed"
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
            inferred_name = str(products[0].get("title") or "Submitted document")
        submitted_products = [
            prepared_products_by_index[idx] for idx in successful_indexes
        ]
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
        "partial_count": partial_count,
        "results": results,
        "submitted_id": submitted_id,
        "duration_ms": submit_duration_ms,
        "warnings": warnings,
        "error": error_message,
    }
