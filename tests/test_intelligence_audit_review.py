from types import SimpleNamespace

from application.use_cases.intelligence_get_audit_review import execute


class AuditReviewIntelligence:
    def get_product_intelligence_audit(self, audit_id, *, shop_domain):
        assert audit_id == "audit-1"
        assert shop_domain == "review.myshopify.com"
        return {
            "audit_id": audit_id,
            "status": "success",
            "enhancement_status": "ready",
            "enhancement_attempts": 1,
            "ai_suggestions_count": 1,
            "derived_suggestions_count": 1,
            "findings": [
                {
                    "finding_id": "finding-1",
                    "product_index": 0,
                    "product_id": "gid://shopify/Product/42",
                    "message": "Description needs detail.",
                }
            ],
            "totals": {
                "audited_products": [
                    {
                        "id": "gid://shopify/Product/42",
                        "title": "Trail Jacket",
                        "vendor": "Northwind",
                    }
                ]
            },
        }

    def list_product_intelligence_suggestions(self, *, audit_id, shop_domain):
        assert audit_id == "audit-1"
        assert shop_domain == "review.myshopify.com"
        return [
            {
                "suggestion_id": "applied-1",
                "finding_id": "finding-1",
                "product_index": 0,
                "product_id": "gid://shopify/Product/42",
                "message": "Improve the description",
                "patch_payload": {"body_html": "Detailed trail jacket"},
                "status": "applied",
                "value_origin": "ai_assisted",
            },
            {
                "suggestion_id": "reverted-1",
                "finding_id": "finding-1",
                "product_index": "invalid",
                "product_id": "gid://shopify/Product/42",
                "message": "Normalize tags",
                "patch_payload": {"tags": ["trail"]},
                "status": "reverted",
                "value_origin": "derived",
            },
        ]


def test_audit_review_keeps_enhanced_history_visible_without_pending_changes():
    review = execute(
        supabase=SimpleNamespace(intelligence=AuditReviewIntelligence()),
        audit_id="audit-1",
        shop_domain="review.myshopify.com",
    )

    assert review is not None
    assert review["review_required"] is True
    assert review["enhancement"] == {
        "status": "ready",
        "error_code": None,
        "error_message": None,
        "attempts": 1,
        "ai_suggestions_count": 1,
        "derived_suggestions_count": 1,
    }
    assert len(review["products"]) == 1
    product = review["products"][0]
    assert product["product_title"] == "Trail Jacket"
    assert product["pending_reviews"] == []
    assert [row["suggestion_id"] for row in product["enhanced_changes"]] == [
        "applied-1",
        "reverted-1",
    ]
    assert len(product["history"]) == 2
