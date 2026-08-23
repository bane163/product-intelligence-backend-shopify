from services.supabase_service import SupabaseService
from types import SimpleNamespace

from api.agents.drafts import _enrich_usage


SHOP = "credits.myshopify.com"


def test_credit_lifecycle_projection_and_tenant_isolation(monkeypatch):
    service = SupabaseService()
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    service._credit_manager._operations = {
        "reserved": {"operation_id": "reserved", "shop_domain": SHOP, "status": "reserved", "reserved_credits": 123},
        "settled": {"operation_id": "settled", "shop_domain": SHOP, "status": "settled", "settled_credits": 90, "reversed_credits": 10},
        "released": {"operation_id": "released", "shop_domain": SHOP, "status": "released", "reserved_credits": 50},
        "foreign": {"operation_id": "foreign", "shop_domain": "other.myshopify.com", "status": "settled", "settled_credits": 999},
    }
    rows = [
        {"billing_operation_id": "reserved"},
        {"billing_operation_id": "settled"},
        {"billing_operation_id": "released"},
        {"billing_operation_id": "foreign"},
        {},
    ]
    service._enrich_run_credit_usage(rows, shop_domain=SHOP)
    assert rows[0]["credit_usage"] == {"amount": 123, "state": "estimated", "operation_count": 1}
    assert rows[1]["credit_usage"] == {"amount": 80, "state": "actual", "operation_count": 1}
    assert rows[2]["credit_usage"] == {"amount": 0, "state": "actual", "operation_count": 1}
    assert rows[3]["credit_usage"] is None
    assert rows[4]["credit_usage"] is None


def test_draft_credit_usage_deduplicates_related_operations():
    class Runs:
        def get_run_summaries(self, run_ids, *, shop_domain):
            assert shop_domain == SHOP
            return {
                "run-1": {"billing_operation_id": "shared", "credit_usage": {"amount": 40, "state": "actual", "operation_count": 1}},
                "run-2": {"billing_operation_id": "shared", "credit_usage": {"amount": 40, "state": "actual", "operation_count": 1}},
                "run-3": {"billing_operation_id": "pending", "credit_usage": {"amount": 60, "state": "estimated", "operation_count": 1}},
            }

    drafts = [{"run_id": "run-1", "extraction_run_id": "run-2", "submit_run_id": "run-3"}]
    ctx = SimpleNamespace(supabase=SimpleNamespace(runs=Runs()))
    _enrich_usage(drafts, ctx, SHOP)
    assert drafts[0]["credit_usage"] == {
        "amount": 100,
        "state": "estimated",
        "operation_count": 2,
    }
