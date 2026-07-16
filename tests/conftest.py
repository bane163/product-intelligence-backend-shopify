import pytest

from app_context import get_app_context
import shopify_session_store


@pytest.fixture(autouse=True)
def isolated_offline_services(monkeypatch):
    """Keep the default suite deterministic and prohibit accidental network I/O."""
    ctx = get_app_context()
    monkeypatch.setenv("WOPI_SIGNING_KEY", "test-wopi-key")
    service = getattr(ctx.services.supabase, "_service", None)
    if service is None:
        yield
        return

    monkeypatch.setattr(service, "_try_get_bucket", lambda: None, raising=False)
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None, raising=False)
    monkeypatch.setattr(
        shopify_session_store,
        "get_offline_access_token",
        lambda _shop: None,
    )

    for name in (
        "file_storage",
        "offload_jobs",
        "llm_runs",
        "llm_run_events",
        "product_drafts",
        "submitted_documents",
        "product_intelligence_audits",
        "product_intelligence_findings",
        "product_intelligence_suggestions",
        "product_intelligence_bulk_operations",
        "product_intelligence_normalization_settings",
        "llm_model_configs",
        "_billing_store",
    ):
        value = getattr(service, name, None)
        if hasattr(value, "clear"):
            value.clear()

    yield
