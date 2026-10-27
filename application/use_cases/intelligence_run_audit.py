from __future__ import annotations

import uuid
from typing import Any

from application.ports.supabase_port import SupabaseNamespacedPort

SCORING_VERSION = "product-mean-v2"
COMPONENT_WEIGHTS = {
    "completeness": 25,
    "consistency": 20,
    "seo_readiness": 20,
    "ai_discoverability": 20,
    "compliance": 10,
    "enrichment": 5,
}


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _add_finding(
    findings: list[dict[str, Any]],
    *,
    product_index: int,
    product_title: str,
    category: str,
    severity: str,
    code: str,
    message: str,
    suggestion: str,
    field_path: str,
    patch_payload: dict[str, Any] | None = None,
    product_id: str = "",
) -> None:
    finding_id = str(uuid.uuid4())
    findings.append(
        {
            "finding_id": finding_id,
            "product_index": product_index,
            "product_title": product_title,
            "product_id": product_id,
            "category": category,
            "severity": severity,
            "code": code,
            "message": message,
            "suggestion": suggestion,
            "field_path": field_path,
            "patch_payload": patch_payload or {},
        }
    )


def _score_product(
    product: dict[str, Any], *, product_index: int = 0
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    dimensions = {
        "completeness": 100,
        "consistency": 100,
        "seo_readiness": 100,
        "ai_discoverability": 100,
        "compliance": 100,
        "enrichment": 100,
    }

    index = product_index
    product_id = _to_text(product.get("id"))
    title = _to_text(product.get("title")) or f"Product #{index + 1}"
    vendor = _to_text(product.get("vendor"))
    body_html = _to_text(product.get("body_html") or product.get("descriptionHtml"))
    product_type = _to_text(product.get("product_type") or product.get("productType"))
    seo_title = _to_text(product.get("seo_title"))
    seo_description = _to_text(product.get("seo_description"))
    tags = _to_tags(product.get("tags"))
    status = _to_text(product.get("status")).lower()
    variants = (
        product.get("variants") if isinstance(product.get("variants"), list) else []
    )

    if not _to_text(product.get("title")):
        dimensions["completeness"] -= 20
        _add_finding(
            findings,
            product_index=index,
            product_title=title,
            category="completeness",
            severity="high",
            code="missing_title",
            message="Product title is missing.",
            suggestion="Provide a descriptive product title.",
            field_path="title",
            patch_payload={"title": title},
        )
    if not vendor:
        dimensions["completeness"] -= 8
        _add_finding(
            findings,
            product_index=index,
            product_title=title,
            category="completeness",
            severity="medium",
            code="missing_vendor",
            message="Vendor is missing.",
            suggestion="Set the product vendor/brand.",
            field_path="vendor",
            patch_payload={"vendor": "Unknown Vendor"},
        )
    if not body_html:
        dimensions["completeness"] -= 10
        _add_finding(
            findings,
            product_index=index,
            product_title=title,
            category="completeness",
            severity="high",
            code="missing_description",
            message="Product description is missing.",
            suggestion="Add a detailed product description.",
            field_path="body_html",
        )
    if not product_type:
        dimensions["completeness"] -= 6
        _add_finding(
            findings,
            product_index=index,
            product_title=title,
            category="completeness",
            severity="low",
            code="missing_product_type",
            message="Product type/category is missing.",
            suggestion="Set product_type to improve structure.",
            field_path="product_type",
            patch_payload={"product_type": "General"},
        )

    if status and status not in {"active", "draft", "archived"}:
        dimensions["consistency"] -= 12
        _add_finding(
            findings,
            product_index=index,
            product_title=title,
            category="consistency",
            severity="medium",
            code="unexpected_status",
            message=f"Status '{status}' is non-standard.",
            suggestion="Use active, draft, or archived.",
            field_path="status",
        )
    if variants and all(
        not _to_text(v.get("sku")) for v in variants if isinstance(v, dict)
    ):
        dimensions["consistency"] -= 8
        _add_finding(
            findings,
            product_index=index,
            product_title=title,
            category="consistency",
            severity="low",
            code="missing_variant_sku",
            message="Variants are present but SKUs are missing.",
            suggestion="Add SKUs to improve catalog consistency.",
            field_path="variants[].sku",
        )

    if len(_to_text(product.get("title"))) < 20:
        dimensions["seo_readiness"] -= 12
        _add_finding(
            findings,
            product_index=index,
            product_title=title,
            category="seo_readiness",
            severity="medium",
            code="short_title",
            message="Title is short for SEO.",
            suggestion="Use a more descriptive, keyword-rich title.",
            field_path="title",
        )
    if not seo_title:
        dimensions["seo_readiness"] -= 10
        _add_finding(
            findings,
            product_index=index,
            product_title=title,
            category="seo_readiness",
            severity="low",
            code="missing_seo_title",
            message="SEO title is missing.",
            suggestion="Provide an SEO title.",
            field_path="seo_title",
            patch_payload={"seo_title": title[:70]},
        )
    if not seo_description and len(body_html) < 120:
        dimensions["seo_readiness"] -= 12
        _add_finding(
            findings,
            product_index=index,
            product_title=title,
            category="seo_readiness",
            severity="medium",
            code="missing_or_short_seo_description",
            message="SEO description is missing or too short.",
            suggestion="Provide an SEO description around 140-160 chars.",
            field_path="seo_description",
            patch_payload={
                "seo_description": (
                    (body_html[:157] + "...")
                    if len(body_html) > 160
                    else (body_html or f"Buy {title} from our catalog.")
                )
            },
        )

    if not tags:
        dimensions["ai_discoverability"] -= 10
        _add_finding(
            findings,
            product_index=index,
            product_title=title,
            category="ai_discoverability",
            severity="low",
            code="missing_tags",
            message="Tags are missing.",
            suggestion="Add structured tags to improve discoverability.",
            field_path="tags",
            patch_payload={
                "tags": ", ".join([t for t in [vendor, product_type] if t]) or "General"
            },
        )
    if not _to_text(
        product.get("product_category") or product.get("google_shopping_category")
    ):
        dimensions["ai_discoverability"] -= 12
        _add_finding(
            findings,
            product_index=index,
            product_title=title,
            category="ai_discoverability",
            severity="medium",
            code="missing_category_mapping",
            message="No taxonomy/category mapping found.",
            suggestion="Map to a product category/taxonomy.",
            field_path="product_category",
            patch_payload={"product_category": product_type or "General"},
        )

    if any(word in body_html.lower() for word in ["guaranteed cure", "100% safe"]):
        dimensions["compliance"] -= 20
        _add_finding(
            findings,
            product_index=index,
            product_title=title,
            category="compliance",
            severity="high",
            code="risky_claim",
            message="Potentially risky claim language detected.",
            suggestion="Review and moderate regulated or absolute claims.",
            field_path="body_html",
        )

    if (
        body_html
        and "\n" not in body_html
        and "•" not in body_html
        and "- " not in body_html
    ):
        dimensions["enrichment"] -= 8
        _add_finding(
            findings,
            product_index=index,
            product_title=title,
            category="enrichment",
            severity="low",
            code="missing_feature_bullets",
            message="Description lacks structured feature bullets.",
            suggestion="Add bullet-point features for readability.",
            field_path="body_html",
        )

    for key, value in dimensions.items():
        dimensions[key] = max(0, min(100, value))
    for finding in findings:
        finding["product_id"] = product_id

    return dimensions, findings


def _score_products(
    products: list[dict[str, Any]],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Compatibility helper returning catalog means and product-tagged findings."""
    results = _build_product_results(products)
    component_scores = {
        key: int(
            round(sum(row["component_scores"][key] for row in results) / len(results))
        )
        for key in COMPONENT_WEIGHTS
    }
    return component_scores, [finding for row in results for finding in row["findings"]]


def _build_product_results(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, product in enumerate(products):
        component_scores, findings = _score_product(product, product_index=index)
        score = int(
            round(
                sum(
                    component_scores[key] * weight
                    for key, weight in COMPONENT_WEIGHTS.items()
                )
                / 100
            )
        )
        results.append(
            {
                "product_id": _to_text(product.get("id")),
                "product_title": _to_text(product.get("title")),
                "score": score,
                "component_scores": component_scores,
                "findings_count": len(findings),
                "audited_snapshot": dict(product),
                "scoring_version": SCORING_VERSION,
                "findings": findings,
            }
        )
    return results


async def execute(
    *,
    supabase: SupabaseNamespacedPort,
    products: list[dict[str, Any]],
    submitted_id: str | None = None,
    run_id: str | None = None,
    shop_domain: str | None = None,
    trace_event: Any | None = None,
) -> dict[str, Any]:
    from application.use_cases.intelligence_generate_suggestions import (
        execute as generate_suggestions_execute,
    )

    if not products:
        raise ValueError("No products provided for intelligence audit")
    tenant = str(shop_domain or "").strip().lower()
    if not tenant:
        raise ValueError("Missing shop_domain for intelligence audit")

    if callable(trace_event):
        trace_event(
            phase="audit_scoring_start",
            message="Scoring products for intelligence audit",
            payload_preview={"products_count": len(products)},
        )
    product_results = _build_product_results(products)
    component_scores = {
        key: int(
            round(
                sum(row["component_scores"][key] for row in product_results)
                / len(product_results)
            )
        )
        for key in COMPONENT_WEIGHTS
    }
    findings = [finding for row in product_results for finding in row["findings"]]
    if callable(trace_event):
        trace_event(
            phase="audit_scoring_done",
            message="Computed audit findings and component scores",
            payload_preview={
                "findings_count": len(findings),
                "component_scores": component_scores,
            },
        )
    overall_score = int(
        round(sum(row["score"] for row in product_results) / len(product_results))
    )

    totals = {
        "products_analyzed": len(products),
        "critical_findings": sum(
            1 for item in findings if item.get("severity") == "high"
        ),
        "high_findings": sum(1 for item in findings if item.get("severity") == "high"),
        "medium_findings": sum(
            1 for item in findings if item.get("severity") == "medium"
        ),
        "low_findings": sum(1 for item in findings if item.get("severity") == "low"),
        "audited_products": [
            {
                "id": _to_text(product.get("id")),
                "title": _to_text(product.get("title")),
                "handle": _to_text(product.get("handle")),
                "body_html": product.get("body_html"),
                "vendor": product.get("vendor"),
                "product_type": product.get("product_type"),
                "tags": product.get("tags"),
                "seo_title": product.get("seo_title"),
                "seo_description": product.get("seo_description"),
            }
            for product in products
        ],
    }

    # Generate every child in memory before creating a visible audit record.
    normalization_settings = (
        supabase.intelligence.get_product_intelligence_normalization_settings(
            shop_domain=tenant,
        )
    )
    generation_kwargs = {
        "supabase": supabase,
        "products": products,
        "shop_domain": tenant,
        "normalization_settings": normalization_settings,
        "trace_event": trace_event if callable(trace_event) else None,
    }
    try:
        generation = await generate_suggestions_execute(
            **generation_kwargs,
            include_status=True,
        )
    except TypeError as exc:
        # Preserve compatibility with older injected generators while the
        # built-in generator returns the richer enhancement status envelope.
        if "include_status" not in str(exc):
            raise
        generation = await generate_suggestions_execute(**generation_kwargs)
    if isinstance(generation, dict):
        suggestions = [
            item for item in generation.get("suggestions", []) if isinstance(item, dict)
        ]
        enhancement_status = str(generation.get("enhancement_status") or "failed")
        enhancement_error_code = generation.get("enhancement_error_code")
        enhancement_error_message = generation.get("enhancement_error_message")
        enhancement_attempts = int(generation.get("enhancement_attempts") or 0)
        ai_suggestions_count = int(generation.get("ai_suggestions_count") or 0)
        derived_suggestions_count = int(
            generation.get("derived_suggestions_count") or 0
        )
    else:
        # Compatibility for injected/test generators and older adapters.
        suggestions = [item for item in generation if isinstance(item, dict)]
        enhancement_status = "ready"
        enhancement_error_code = None
        enhancement_error_message = None
        enhancement_attempts = 1
        ai_suggestions_count = sum(
            1 for item in suggestions if item.get("value_origin") == "ai_assisted"
        )
        derived_suggestions_count = sum(
            1 for item in suggestions if item.get("value_origin") == "derived"
        )

    audit_id = str(uuid.uuid4())
    audit_created = False
    try:
        audit = supabase.intelligence.save_product_intelligence_audit(
            audit_id=audit_id,
            run_id=run_id,
            submitted_id=submitted_id,
            scope="submitted_document" if submitted_id else "adhoc_products",
            status="pending",
            overall_score=overall_score,
            findings_count=len(findings),
            component_scores=component_scores,
            totals=totals,
            shop_domain=tenant,
            scoring_version=SCORING_VERSION,
            enhancement_status=enhancement_status,
            enhancement_error_code=(
                str(enhancement_error_code) if enhancement_error_code else None
            ),
            enhancement_error_message=(
                str(enhancement_error_message) if enhancement_error_message else None
            ),
            enhancement_attempts=enhancement_attempts,
            ai_suggestions_count=ai_suggestions_count,
            derived_suggestions_count=derived_suggestions_count,
            require_persistence=True,
        )
        audit_created = True
        if callable(trace_event):
            trace_event(
                phase="audit_persisted",
                message="Persisted pending audit metadata",
                payload_preview={"audit_id": audit_id, "run_id": run_id},
            )
        supabase.intelligence.save_product_intelligence_findings(
            audit_id=audit_id,
            findings=findings,
            shop_domain=tenant,
            require_persistence=True,
        )
        supabase.intelligence.save_product_intelligence_product_results(
            audit_id=audit_id,
            results=[
                {key: value for key, value in row.items() if key != "findings"}
                for row in product_results
            ],
            shop_domain=tenant,
            require_persistence=True,
        )
        supabase.intelligence.save_product_intelligence_suggestions(
            audit_id=audit_id,
            suggestions=suggestions,
            shop_domain=tenant,
            require_persistence=True,
        )
        if callable(trace_event):
            trace_event(
                phase="children_persisted",
                message="Persisted intelligence findings, results, and suggestions",
                payload_preview={
                    "findings_count": len(findings),
                    "suggestions_count": len(suggestions),
                },
            )
        if enhancement_status == "ready":
            supabase.intelligence.supersede_pending_product_intelligence_suggestions(
                product_ids=[str(product.get("id") or "") for product in products],
                superseded_by_audit_id=audit_id,
                shop_domain=tenant,
                require_persistence=True,
            )
        final_status = "success" if enhancement_status == "ready" else "needs_attention"
        completed = supabase.intelligence.update_product_intelligence_audit_status(
            audit_id=audit_id,
            status=final_status,
            shop_domain=tenant,
            require_persistence=True,
        )
        if not completed:
            raise RuntimeError("Failed to mark product intelligence audit successful")
    except Exception:
        if audit_created:
            try:
                supabase.intelligence.update_product_intelligence_audit_status(
                    audit_id=audit_id,
                    status="error",
                    shop_domain=tenant,
                    require_persistence=False,
                )
            except Exception:
                pass
        raise

    return {
        **audit,
        **completed,
        "status": final_status,
        "review_required": True,
        "findings": findings,
        "suggestions": suggestions,
    }
