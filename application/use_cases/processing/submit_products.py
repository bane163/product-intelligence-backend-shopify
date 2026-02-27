import uuid
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from application.ports.shopify_port import ShopifyPort
from application.ports.supabase_port import SupabaseNamespacedPort
from application.ports.tracing_port import TracingPort
from application.use_cases.intelligence_apply_suggestion import (
    _extract_user_errors,
    _normalize_metafields_payload,
    _normalize_variant_operations,
)
from application.use_cases.intelligence_generate_suggestions import (
    execute as generate_suggestions_execute,
)

from api.agents.utils import normalize_shop_domain, parse_products_json


SUPPORTED_SUGGESTION_FIELDS = {
    "title",
    "handle",
    "body_html",
    "vendor",
    "product_type",
    "product_category",
    "status",
    "seo_title",
    "seo_description",
    "tags",
    "metafields",
}


def _optional_str(data: dict[str, Any] | None, key: str) -> str | None:
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _normalize_tags(value: Any) -> str | list[str] | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return None


def _merge_metafields(existing_raw: Any, incoming_raw: Any) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in _normalize_metafields_payload(existing_raw):
        merged[(item["namespace"], item["key"])] = item
    for item in _normalize_metafields_payload(incoming_raw):
        merged[(item["namespace"], item["key"])] = item
    return list(merged.values())


def _apply_patch_payload(
    *,
    product: dict[str, Any],
    patch_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    updated = dict(product)
    for key in SUPPORTED_SUGGESTION_FIELDS:
        if key not in patch_payload:
            continue
        if key == "metafields":
            merged = _merge_metafields(
                updated.get("metafields"), patch_payload.get("metafields")
            )
            if merged:
                updated["metafields"] = merged
            continue
        if key == "tags":
            tags = _normalize_tags(patch_payload.get("tags"))
            if tags is not None:
                updated["tags"] = tags
            continue
        value = patch_payload.get(key)
        if value is None:
            continue
        updated[key] = value
    variant_operations = _normalize_variant_operations(
        patch_payload.get("variant_operations")
    )
    return updated, variant_operations


def _apply_ai_suggestions(
    *,
    products: list[dict[str, Any]],
    suggestions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    enhanced_products = [dict(item) for item in products]
    variant_operations_by_index: dict[int, list[dict[str, Any]]] = {}
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            continue
        patch_payload = suggestion.get("patch_payload")
        if not isinstance(patch_payload, dict) or not patch_payload:
            continue
        try:
            product_index = int(suggestion.get("product_index"))
        except (TypeError, ValueError):
            continue
        if product_index < 0 or product_index >= len(enhanced_products):
            continue
        updated, variant_operations = _apply_patch_payload(
            product=enhanced_products[product_index],
            patch_payload=patch_payload,
        )
        enhanced_products[product_index] = updated
        if variant_operations.get("create_options") or variant_operations.get(
            "create_variants"
        ):
            variant_operations_by_index.setdefault(product_index, []).append(
                variant_operations
            )
    return enhanced_products, variant_operations_by_index


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


def _sync_enhanced_products_to_draft(
    *,
    supabase: SupabaseNamespacedPort,
    draft_id: str,
    run_id: str,
    import_mode: str,
    products: list[dict[str, Any]],
) -> bool:
    draft = supabase.drafts.get_product_draft(draft_id)
    if not isinstance(draft, dict):
        return False
    supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=run_id,
        import_mode=_optional_str(draft, "import_mode") or import_mode,
        draft_name=_optional_str(draft, "draft_name"),
        input_file_id=_optional_str(draft, "input_file_id"),
        input_filename=_optional_str(draft, "input_filename"),
        output_file_id=None,
        output_filename=None,
        products=products,
    )
    return True


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
    enable_ai_enhancements: bool = False,
) -> dict[str, object]:
    submit_started_at = perf_counter()
    current_run_id = run_id or str(uuid.uuid4())
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
            "ai_enhancements_enabled": bool(enable_ai_enhancements),
            "attempt": 1,
            "shop_domain": shop_domain,
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
    submit_client = shopify

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

    submitted_document: dict[str, Any] | None = None
    draft_document: dict[str, Any] | None = None
    products: list[dict[str, Any]] = []
    submitted_source_id = (
        submitted_id.strip()
        if isinstance(submitted_id, str) and submitted_id.strip()
        else None
    )
    draft_source_id = (
        draft_id.strip() if isinstance(draft_id, str) and draft_id.strip() else None
    )
    if submitted_source_id:
        submitted_document = supabase.submitted.get_submitted_document(
            submitted_source_id
        )
        if not isinstance(submitted_document, dict):
            fail_submit("Submitted document not found")
        stored_products = submitted_document.get("products")
        if not isinstance(stored_products, list):
            fail_submit("Submitted document has invalid products payload")
        products = [item for item in stored_products if isinstance(item, dict)]
        if not draft_id:
            inferred_draft_id = submitted_document.get("draft_id")
            if isinstance(inferred_draft_id, str) and inferred_draft_id.strip():
                draft_id = inferred_draft_id.strip()
        if not document_name:
            inferred_name = submitted_document.get("name")
            if isinstance(inferred_name, str) and inferred_name.strip():
                document_name = inferred_name.strip()
    elif draft_source_id:
        draft_document = supabase.drafts.get_product_draft(draft_source_id)
        if not isinstance(draft_document, dict):
            fail_submit("Draft not found")
        stored_products = draft_document.get("products")
        if not isinstance(stored_products, list):
            fail_submit("Draft has invalid products payload")
        products = [item for item in stored_products if isinstance(item, dict)]
        draft_id = draft_source_id
        if not document_name:
            inferred_name = draft_document.get("draft_name") or draft_document.get(
                "first_product_title"
            )
            if isinstance(inferred_name, str) and inferred_name.strip():
                document_name = inferred_name.strip()
    elif isinstance(products_json, str) and products_json.strip():
        products = parse_products_json(products_json)
    if not products:
        fail_submit("No products provided")

    variant_operations_by_index: dict[int, list[dict[str, Any]]] = {}
    tenant = normalize_shop_domain(shop_domain)
    if enable_ai_enhancements:
        if not tenant:
            fail_submit("Missing shop_domain for AI enhancements")
        try:
            normalization_settings = (
                supabase.intelligence.get_product_intelligence_normalization_settings(
                    shop_domain=tenant
                )
            )
            suggestions = await generate_suggestions_execute(
                supabase=supabase,
                products=products,
                shop_domain=tenant,
                normalization_settings=normalization_settings,
                trace_event=emitter.trace_event if emitter else None,
            )
            products, variant_operations_by_index = _apply_ai_suggestions(
                products=products,
                suggestions=suggestions,
            )
            emit_and_persist(
                phase="submit_ai_enhancements_applied",
                message="Applied AI enhancement suggestions before submit",
                payload_preview={
                    "suggestions_count": len(suggestions),
                    "products_count": len(products),
                    "variant_operations_count": sum(
                        len(items) for items in variant_operations_by_index.values()
                    ),
                },
            )
            if isinstance(draft_id, str) and draft_id.strip():
                synced_draft = _sync_enhanced_products_to_draft(
                    supabase=supabase,
                    draft_id=draft_id,
                    run_id=current_run_id,
                    import_mode=import_mode,
                    products=products,
                )
                emit_and_persist(
                    phase="submit_draft_enhancements_synced",
                    message=(
                        "Synced enhanced products into draft for preview regeneration"
                        if synced_draft
                        else "Skipped draft sync; draft was not found"
                    ),
                    payload_preview={"draft_id": draft_id, "synced": synced_draft},
                )
        except Exception as exc:
            fail_submit(f"AI enhancements failed: {exc}")

    emit_and_persist(
        phase="submit_products_loaded",
        message="Loaded products for submit",
        payload_preview={
            "count": len(products),
            "mode": import_mode,
            "shop_domain": tenant,
            "has_token": bool(shop_access_token),
            "ai_enhancements_enabled": bool(enable_ai_enhancements),
            "source": (
                "submitted_document"
                if submitted_source_id
                else "draft"
                if draft_source_id
                else "products_json"
            ),
        },
    )

    from application.domain.product import extract_first_sku

    results: list[dict[str, Any]] = []
    success_count = 0
    handle_lookup_cache: dict[str, str | None] = {}
    sku_lookup_cache: dict[str, str | None] = {}
    for index, product in enumerate(products):
        item_started_at = perf_counter()
        title = product.get("title")
        if not title:
            emit_and_persist(
                phase="submit_item_error",
                message=f"Product at index {index} missing title",
                level="error",
                error="Missing title",
                payload_preview={"index": index},
            )
            results.append(
                {
                    "index": index,
                    "title": None,
                    "status": "failed",
                    "errors": [{"field": ["title"], "message": "Missing title"}],
                }
            )
            continue
        try:
            lookup_started_at = perf_counter()
            gid = product.get("id") or product.get("shopify_gid")
            resolve_reason = "id" if gid else None
            if not gid and product.get("handle"):
                handle_key = str(product["handle"]).strip().lower()
                if handle_key in handle_lookup_cache:
                    gid = handle_lookup_cache[handle_key]
                else:
                    gid = await submit_client.find_product_id_by_handle(
                        str(product["handle"])
                    )
                    handle_lookup_cache[handle_key] = gid
                if gid:
                    resolve_reason = "handle"
            if not gid:
                sku = extract_first_sku(product)
                if sku:
                    sku_key = str(sku).strip()
                    if sku_key in sku_lookup_cache:
                        gid = sku_lookup_cache[sku_key]
                    else:
                        gid = await submit_client.find_product_id_by_sku(sku)
                        sku_lookup_cache[sku_key] = gid
                    if gid:
                        resolve_reason = "sku"
            lookup_duration_ms = int((perf_counter() - lookup_started_at) * 1000)

            if gid:
                emit_and_persist(
                    phase="submit_item_mode",
                    message=f"Resolved update target for '{title}'",
                    payload_preview={
                        "index": index,
                        "title": title,
                        "mode": "update",
                        "reason": resolve_reason,
                        "lookup_duration_ms": lookup_duration_ms,
                        "shopify_product_id": gid,
                    },
                )
                mutation_started_at = perf_counter()
                response = await submit_client.update_product_from_input(
                    {**product, "id": gid}
                )
                payload = response.get("data", {}).get("productUpdate", {})
                mode_used = "update"
            else:
                emit_and_persist(
                    phase="submit_item_mode",
                    message=f"No existing match found for '{title}', creating",
                    payload_preview={
                        "index": index,
                        "title": title,
                        "mode": "create",
                        "lookup_duration_ms": lookup_duration_ms,
                    },
                )
                mutation_started_at = perf_counter()
                response = await submit_client.create_product_from_input(product)
                payload = response.get("data", {}).get("productCreate", {})
                mode_used = "create"
            mutation_duration_ms = int((perf_counter() - mutation_started_at) * 1000)

            errors = payload.get("userErrors") or []
            product_data = payload.get("product") or {}
            if errors:
                emit_and_persist(
                    phase="submit_item_error",
                    message=f"Shopify rejected product '{title}'",
                    level="error",
                    error=str(errors),
                    payload_preview={"index": index, "title": title},
                )
                results.append(
                    {
                        "index": index,
                        "title": title,
                        "status": "failed",
                        "shopify_product_id": product_data.get("id"),
                        "errors": errors,
                    }
                )
            else:
                if mode_used == "create":
                    variant_operations = variant_operations_by_index.get(index) or []
                    if variant_operations:
                        created_product_id = str(product_data.get("id") or "").strip()
                        if not created_product_id:
                            raise RuntimeError(
                                "AI enhancements generated variant operations, but Shopify product ID is missing"
                            )
                        await _apply_variant_operations_for_product(
                            shopify=submit_client,
                            product_id=created_product_id,
                            operations=variant_operations,
                        )
                success_count += 1
                emit_and_persist(
                    phase="submit_item_success",
                    message=f"Submitted product '{product_data.get('title', title)}' via {mode_used}",
                    payload_preview={
                        "index": index,
                        "mode": mode_used,
                        "lookup_duration_ms": lookup_duration_ms,
                        "mutation_duration_ms": mutation_duration_ms,
                        "item_duration_ms": int(
                            (perf_counter() - item_started_at) * 1000
                        ),
                        "shopify_product_id": product_data.get("id"),
                    },
                )
                results.append(
                    {
                        "index": index,
                        "title": product_data.get("title", title),
                        "status": "success",
                        "mode": mode_used,
                        "shopify_product_id": product_data.get("id"),
                        "lookup_duration_ms": lookup_duration_ms,
                        "mutation_duration_ms": mutation_duration_ms,
                        "errors": [],
                    }
                )
        except Exception as exc:
            emit_and_persist(
                phase="submit_item_error",
                message=f"Submit failed for product '{title}'",
                level="error",
                error=str(exc),
                payload_preview={"index": index, "title": title},
            )
            results.append(
                {
                    "index": index,
                    "title": title,
                    "status": "failed",
                    "item_duration_ms": int((perf_counter() - item_started_at) * 1000),
                    "errors": [{"field": None, "message": str(exc)}],
                }
            )

    status = "success" if success_count == len(products) else "error"
    submitted_id: str | None = None
    if status == "success":
        inferred_name = document_name
        if not inferred_name and draft_id:
            draft = supabase.drafts.get_product_draft(draft_id)
            if draft:
                inferred_name = draft.get("draft_name") or draft.get(
                    "first_product_title"
                )
        if not inferred_name:
            inferred_name = str(products[0].get("title") or "Submitted document")
        submitted = supabase.submitted.save_submitted_document(
            submitted_id=str(uuid.uuid4()),
            run_id=current_run_id,
            draft_id=draft_id,
            name=str(inferred_name),
            import_mode="auto",
            product_count=len(products),
            products=products,
        )
        submitted_id = str(submitted.get("submitted_id"))

    submit_duration_ms = int((perf_counter() - submit_started_at) * 1000)
    supabase.runs.finalize_run(current_run_id, status=status, duration_ms=submit_duration_ms)
    tracing.complete_run(current_run_id)

    return {
        "success_count": success_count,
        "results": results,
        "submitted_id": submitted_id,
        "duration_ms": submit_duration_ms,
    }
