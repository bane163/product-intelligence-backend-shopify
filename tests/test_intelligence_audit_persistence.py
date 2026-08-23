from types import SimpleNamespace
from pathlib import Path

import pytest

import application.use_cases.intelligence_generate_suggestions as suggestions_uc
from application.use_cases.intelligence_run_audit import execute
from services.supabase_intelligence_mixin import _suggestion_persistence_payload


SHOP = "strict-audit.myshopify.com"
PRODUCT = {
    "id": "gid://shopify/Product/101",
    "title": "Cool Mug",
    "vendor": "Test Vendor",
    "product_type": "Mugs",
    "body_html": "A detailed product description with enough useful catalog content.",
    "seo_title": "Cool Mug",
    "seo_description": "A useful search description for this cool mug.",
    "tags": ["mug"],
    "status": "active",
    "product_category": "Mugs",
}


class RecordingIntelligence:
    def __init__(self, fail_at: str | None = None):
        self.fail_at = fail_at
        self.events: list[tuple[str, str | None]] = []
        self.audit_status = None
        self.prior_status = "pending"
        self.audit_fields: dict = {}

    def get_product_intelligence_normalization_settings(self, **_kwargs):
        return None

    def _write(self, operation: str, status: str | None = None):
        self.events.append((operation, status))
        if self.fail_at == operation:
            raise RuntimeError(f"injected {operation} failure")

    def save_product_intelligence_audit(
        self, *, status, require_persistence, **_kwargs
    ):
        assert require_persistence is True
        self._write("audit", status)
        self.audit_status = status
        self.audit_fields = dict(_kwargs)
        return {"audit_id": _kwargs["audit_id"], "status": status}

    def save_product_intelligence_findings(self, *, require_persistence, **_kwargs):
        assert require_persistence is True
        self._write("findings")
        return len(_kwargs["findings"])

    def save_product_intelligence_product_results(
        self, *, require_persistence, **_kwargs
    ):
        assert require_persistence is True
        self._write("results")
        return len(_kwargs["results"])

    def save_product_intelligence_suggestions(self, *, require_persistence, **_kwargs):
        assert require_persistence is True
        self._write("suggestions")
        return len(_kwargs["suggestions"])

    def supersede_pending_product_intelligence_suggestions(
        self, *, require_persistence, **_kwargs
    ):
        assert require_persistence is True
        self._write("supersede")
        self.prior_status = "superseded"
        return 1

    def update_product_intelligence_audit_status(
        self, *, status, require_persistence, **_kwargs
    ):
        self.events.append(("status", status))
        if require_persistence and self.fail_at == "status":
            raise RuntimeError("injected status failure")
        self.audit_status = status
        return {"audit_id": _kwargs["audit_id"], "status": status}


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", ["findings", "results", "suggestions", "supersede"])
async def test_audit_child_write_failures_mark_error_and_preserve_prior_suggestions(
    monkeypatch, fail_at
):
    async def generated(**_kwargs):
        return [
            {
                "suggestion_id": "new-fix",
                "finding_id": "finding-1",
                "product_index": 0,
                "product_id": PRODUCT["id"],
                "message": "Improve title",
                "patch_payload": {"title": "Better Mug"},
                "status": "pending",
                "value_origin": "ai_assisted",
            }
        ]

    monkeypatch.setattr(suggestions_uc, "execute", generated)
    intelligence = RecordingIntelligence(fail_at)

    with pytest.raises(RuntimeError, match=f"injected {fail_at} failure"):
        await execute(
            supabase=SimpleNamespace(intelligence=intelligence),
            products=[PRODUCT],
            shop_domain=SHOP,
        )

    assert intelligence.audit_status == "error"
    assert intelligence.prior_status == "pending"
    assert ("status", "success") not in intelligence.events


@pytest.mark.asyncio
async def test_audit_becomes_successful_only_after_children_and_superseding(
    monkeypatch,
):
    async def generated(**_kwargs):
        return []

    monkeypatch.setattr(suggestions_uc, "execute", generated)
    intelligence = RecordingIntelligence()
    result = await execute(
        supabase=SimpleNamespace(intelligence=intelligence),
        products=[PRODUCT],
        shop_domain=SHOP,
    )

    assert [event[0] for event in intelligence.events] == [
        "audit",
        "findings",
        "results",
        "suggestions",
        "supersede",
        "status",
    ]
    assert intelligence.events[0] == ("audit", "pending")
    assert intelligence.events[-1] == ("status", "success")
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_failed_ai_enhancement_is_visible_and_does_not_supersede_prior_review(
    monkeypatch,
):
    async def generated(**_kwargs):
        return {
            "suggestions": [],
            "enhancement_status": "failed",
            "enhancement_error_code": "AI_RESPONSE_INVALID",
            "enhancement_error_message": "The model response was invalid.",
            "enhancement_attempts": 2,
            "ai_suggestions_count": 0,
            "derived_suggestions_count": 0,
        }

    monkeypatch.setattr(suggestions_uc, "execute", generated)
    intelligence = RecordingIntelligence()
    result = await execute(
        supabase=SimpleNamespace(intelligence=intelligence),
        products=[PRODUCT],
        shop_domain=SHOP,
    )

    assert result["status"] == "needs_attention"
    assert result["review_required"] is True
    assert intelligence.audit_fields["enhancement_status"] == "failed"
    assert intelligence.audit_fields["enhancement_error_code"] == "AI_RESPONSE_INVALID"
    assert intelligence.audit_fields["enhancement_attempts"] == 2
    assert intelligence.prior_status == "pending"
    assert "supersede" not in [event[0] for event in intelligence.events]


def test_suggestion_persistence_payload_allows_provenance_and_drops_transient_fields():
    payload = _suggestion_persistence_payload(
        {
            "suggestion_id": "one",
            "value_origin": "derived",
            "patch_payload": {"tags": ["normalized"]},
            "model_debug": {"tokens": 10},
            "_generation_context": "transient",
        }
    )
    assert payload["value_origin"] == "derived"
    assert "model_debug" not in payload
    assert "_generation_context" not in payload


def test_value_origin_migration_is_nullable_constrained_and_reloads_postgrest():
    migration = Path(
        "supabase/migrations/20260808000100_add_suggestion_value_origin.sql"
    ).read_text()
    assert "ADD COLUMN IF NOT EXISTS value_origin TEXT NULL" in migration
    assert (
        "'source', 'ai_assisted', 'merchant_edited', 'derived', 'unknown'" in migration
    )
    assert "SET value_origin = NULL" in migration
    assert "NOTIFY pgrst, 'reload schema'" in migration


def test_enhancement_review_state_migration_is_explicit_and_reloadable():
    migration = Path(
        "supabase/migrations/20260823000100_catalog_enhancement_review_state.sql"
    ).read_text()
    assert "ADD COLUMN IF NOT EXISTS enhancement_status" in migration
    assert "'ready', 'failed', 'legacy_unknown'" in migration
    assert "ADD COLUMN IF NOT EXISTS enhancement_error_message" in migration
    assert "ADD COLUMN IF NOT EXISTS ai_suggestions_count" in migration
    assert "NOTIFY pgrst, 'reload schema'" in migration
