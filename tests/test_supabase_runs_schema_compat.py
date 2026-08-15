from types import SimpleNamespace

from services.supabase_service import SupabaseService


class FakeRunsTable:
    def __init__(self, client):
        self.client = client
        self.payload = None

    def upsert(self, payload, on_conflict=None):
        self.payload = dict(payload)
        self.client.calls.append((dict(payload), on_conflict))
        return self

    def execute(self):
        if "enrichment_applied" in (self.payload or {}):
            raise RuntimeError(
                "Could not find the 'enrichment_applied' column of 'llm_runs' in the schema cache"
            )
        return SimpleNamespace(data=[self.payload])


class FakeClient:
    def __init__(self):
        self.calls = []

    def table(self, name):
        assert name == "llm_runs"
        return FakeRunsTable(self)


def test_finalize_run_retries_without_optional_enrichment_columns_on_stale_schema():
    client = FakeClient()
    service = SupabaseService()
    service._get_supabase_client = lambda: client

    service.finalize_run(
        "run-1",
        status="success",
        duration_ms=123,
        extra_fields={
            "enrichment_attempted": False,
            "enrichment_applied": False,
            "enrichment_suggestions_count": 0,
            "enrichment_warning": None,
        },
    )

    assert len(client.calls) == 2
    first, second = client.calls[0][0], client.calls[1][0]
    assert first["status"] == "succeeded"
    assert "enrichment_applied" in first
    assert second["status"] == "succeeded"
    assert second["duration_ms"] == 123
    assert "enrichment_applied" not in second
