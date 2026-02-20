from __future__ import annotations

import uuid
from typing import Any

from application.ports.supabase_port import SupabasePort


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
) -> None:
    finding_id = str(uuid.uuid4())
    findings.append(
        {
            "finding_id": finding_id,
            "product_index": product_index,
            "product_title": product_title,
            "category": category,
            "severity": severity,
            "code": code,
            "message": message,
            "suggestion": suggestion,
            "field_path": field_path,
            "patch_payload": patch_payload or {},
        }
    )


def _score_products(products: list[dict[str, Any]]) -> tuple[dict[str, int], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    dimensions = {
        "completeness": 100,
        "consistency": 100,
        "seo_readiness": 100,
        "ai_discoverability": 100,
        "compliance": 100,
        "enrichment": 100,
    }

    for index, product in enumerate(products):
        title = _to_text(product.get("title")) or f"Product #{index + 1}"
        vendor = _to_text(product.get("vendor"))
        body_html = _to_text(product.get("body_html") or product.get("descriptionHtml"))
        product_type = _to_text(product.get("product_type") or product.get("productType"))
        seo_title = _to_text(product.get("seo_title"))
        seo_description = _to_text(product.get("seo_description"))
        tags = _to_tags(product.get("tags"))
        status = _to_text(product.get("status")).lower()
        variants = product.get("variants") if isinstance(product.get("variants"), list) else []

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
        if variants and all(not _to_text(v.get("sku")) for v in variants if isinstance(v, dict)):
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
                patch_payload={"tags": ", ".join([t for t in [vendor, product_type] if t]) or "General"},
            )
        if not _to_text(product.get("product_category") or product.get("google_shopping_category")):
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

        if body_html and "\n" not in body_html and "•" not in body_html and "- " not in body_html:
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

    return dimensions, findings


async def execute(
    *,
    supabase: SupabasePort,
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
    component_scores, findings = _score_products(products)
    if callable(trace_event):
        trace_event(
            phase="audit_scoring_done",
            message="Computed audit findings and component scores",
            payload_preview={
                "findings_count": len(findings),
                "component_scores": component_scores,
            },
        )
    weights = {
        "completeness": 25,
        "consistency": 20,
        "seo_readiness": 20,
        "ai_discoverability": 20,
        "compliance": 10,
        "enrichment": 5,
    }
    weighted_sum = sum(component_scores[key] * weight for key, weight in weights.items())
    overall_score = int(round(weighted_sum / 100))

    totals = {
        "products_analyzed": len(products),
        "critical_findings": sum(1 for item in findings if item.get("severity") == "high"),
        "high_findings": sum(1 for item in findings if item.get("severity") == "high"),
        "medium_findings": sum(1 for item in findings if item.get("severity") == "medium"),
        "low_findings": sum(1 for item in findings if item.get("severity") == "low"),
        "audited_products": [
            {
                "id": _to_text(product.get("id")),
                "title": _to_text(product.get("title")),
                "handle": _to_text(product.get("handle")),
            }
            for product in products
            if _to_text(product.get("title"))
        ],
    }

    audit_id = str(uuid.uuid4())
    audit = supabase.save_product_intelligence_audit(
        audit_id=audit_id,
        run_id=run_id,
        submitted_id=submitted_id,
        scope="submitted_document" if submitted_id else "adhoc_products",
        status="success",
        overall_score=overall_score,
        findings_count=len(findings),
        component_scores=component_scores,
        totals=totals,
        shop_domain=tenant,
    )
    if callable(trace_event):
        trace_event(
            phase="audit_persisted",
            message="Persisted audit metadata",
            payload_preview={"audit_id": audit_id, "run_id": run_id},
        )
    supabase.save_product_intelligence_findings(audit_id=audit_id, findings=findings, shop_domain=tenant)
    if callable(trace_event):
        trace_event(
            phase="findings_persisted",
            message="Persisted intelligence findings",
            payload_preview={"findings_count": len(findings)},
        )
    normalization_settings = supabase.get_product_intelligence_normalization_settings(
        shop_domain=tenant,
    )
    suggestions = await generate_suggestions_execute(
        supabase=supabase,
        products=products,
        shop_domain=tenant,
        normalization_settings=normalization_settings,
        trace_event=trace_event if callable(trace_event) else None,
    )
    supabase.save_product_intelligence_suggestions(audit_id=audit_id, suggestions=suggestions, shop_domain=tenant)
    if callable(trace_event):
        trace_event(
            phase="suggestions_persisted",
            message="Persisted intelligence suggestions",
            payload_preview={"suggestions_count": len(suggestions)},
        )

    return {**audit, "findings": findings, "suggestions": suggestions}
