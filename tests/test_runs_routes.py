import pytest
from httpx import ASGITransport, AsyncClient

from app_context import get_app_context
from main import app


@pytest.mark.asyncio
async def test_list_runs_requires_shop_domain_header():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/agents/runs")
    assert response.status_code == 400
    assert "shop_domain" in response.text


@pytest.mark.asyncio
async def test_list_runs_passes_tenant_scope(monkeypatch):
    ctx = get_app_context()
    captured: dict[str, object] = {}

    def fake_list_runs(limit=50, offset=0, status=None, shop_domain=None):
        captured["limit"] = limit
        captured["offset"] = offset
        captured["status"] = status
        captured["shop_domain"] = shop_domain
        return [{"run_id": "run-tenant", "status": "running", "shop_domain": shop_domain}]

    monkeypatch.setattr(ctx.services.supabase, "list_runs", fake_list_runs)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/agents/runs?limit=10&offset=2&status=success",
            headers={"x-shop-domain": "TEST-SHOP.myshopify.com"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["runs"][0]["run_id"] == "run-tenant"
    assert captured == {
        "limit": 10,
        "offset": 2,
        "status": "succeeded",
        "shop_domain": "test-shop.myshopify.com",
    }


@pytest.mark.asyncio
async def test_list_runs_echoes_observability_headers(monkeypatch):
    ctx = get_app_context()

    def fake_list_runs(limit=50, offset=0, status=None, shop_domain=None):
        _ = (limit, offset, status, shop_domain)
        return []

    monkeypatch.setattr(ctx.services.supabase, "list_runs", fake_list_runs)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/agents/runs",
            headers={
                "x-shop-domain": "test-shop.myshopify.com",
                "x-request-id": "req-list-runs-1",
                "x-correlation-id": "corr-list-runs-1",
            },
        )

    assert response.status_code == 200
    assert response.headers.get("x-request-id") == "req-list-runs-1"
    assert response.headers.get("x-correlation-id") == "corr-list-runs-1"


@pytest.mark.asyncio
async def test_run_control_resume_retry_cancel(monkeypatch):
    ctx = get_app_context()
    run_state = {
        "run_id": "run-123",
        "status": "failed",
        "attempt": 1,
        "shop_domain": "test-shop.myshopify.com",
        "resume_token": "resume-token-1",
    }
    run_events: list[dict] = []

    def fake_get_run(run_id: str, *, shop_domain=None):
        if run_id != run_state["run_id"]:
            return None
        if shop_domain and shop_domain != run_state.get("shop_domain"):
            return None
        return dict(run_state)

    def fake_get_run_history(run_id: str, *, shop_domain=None):
        run = fake_get_run(run_id, shop_domain=shop_domain)
        if not run:
            return {"run": None, "events": [], "messages": []}
        return {"run": run, "events": list(run_events), "messages": []}

    def fake_create_or_update_run(run_id: str, fields: dict):
        if run_id != run_state["run_id"]:
            return
        run_state.update(fields)

    def fake_append_run_event(run_id: str, event: dict, seq: int):
        run_events.append({"run_id": run_id, **event, "seq": seq})

    def fake_emit_run_event(run_id: str, **kwargs):
        return {"run_id": run_id, **kwargs}

    monkeypatch.setattr(ctx.services.supabase, "get_run", fake_get_run)
    monkeypatch.setattr(ctx.services.supabase, "get_run_history", fake_get_run_history)
    monkeypatch.setattr(ctx.services.supabase, "create_or_update_run", fake_create_or_update_run)
    monkeypatch.setattr(ctx.services.supabase, "append_run_event", fake_append_run_event)
    monkeypatch.setattr(ctx.services.tracing, "emit_run_event", fake_emit_run_event)
    monkeypatch.setattr(ctx.services.tracing, "complete_run", lambda run_id: None)

    control_headers = {
        "x-shop-domain": "test-shop.myshopify.com",
        "x-request-id": "req-run-control-1",
        "x-correlation-id": "corr-run-control-1",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resume_response = await ac.post(
            "/agents/runs/run-123/resume",
            data={"resume_token": "resume-token-1"},
            headers=control_headers,
        )
        assert resume_response.status_code == 200
        assert resume_response.json()["run"]["status"] == "queued"
        assert resume_response.json()["run"]["attempt"] == 2

        run_state.update({"status": "running"})
        cancel_response = await ac.post(
            "/agents/runs/run-123/cancel",
            headers=control_headers,
        )
        assert cancel_response.status_code == 200
        assert cancel_response.json()["run"]["status"] == "cancelled"

        run_state.update({"status": "failed"})
        retry_response = await ac.post(
            "/agents/runs/run-123/retry",
            headers=control_headers,
        )
        assert retry_response.status_code == 200
        assert retry_response.json()["run"]["status"] == "queued"
        assert retry_response.json()["run"]["attempt"] == 3

    assert len(run_events) == 3
    for event in run_events:
        metadata = event.get("metadata") if isinstance(event, dict) else None
        assert isinstance(metadata, dict)
        assert metadata.get("request_id") == "req-run-control-1"
        assert metadata.get("correlation_id") == "corr-run-control-1"


@pytest.mark.asyncio
async def test_workflow_snapshot_returns_run_draft_and_events(monkeypatch):
    ctx = get_app_context()

    def fake_get_run(run_id: str, *, shop_domain=None):
        if run_id != "run-snap" or shop_domain != "test-shop.myshopify.com":
            return None
        return {"run_id": run_id, "status": "running", "shop_domain": shop_domain}

    def fake_get_product_draft(draft_id: str, *, shop_domain=None):
        if draft_id != "draft-snap" or shop_domain != "test-shop.myshopify.com":
            return None
        return {
            "draft_id": draft_id,
            "extraction_run_id": "run-snap",
            "shop_domain": shop_domain,
        }

    def fake_get_run_history(run_id: str, *, shop_domain=None):
        if run_id != "run-snap" or shop_domain != "test-shop.myshopify.com":
            return {"run": None, "events": [], "messages": []}
        return {
            "run": {"run_id": run_id, "status": "running", "shop_domain": shop_domain},
            "events": [
                {"run_id": run_id, "seq": 1, "phase": "workflow_start"},
                {"run_id": run_id, "seq": 2, "phase": "extract_complete"},
            ],
            "messages": [],
        }

    monkeypatch.setattr(ctx.services.supabase, "get_run", fake_get_run)
    monkeypatch.setattr(ctx.services.supabase, "get_product_draft", fake_get_product_draft)
    monkeypatch.setattr(ctx.services.supabase, "get_run_history", fake_get_run_history)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/agents/runs/run-snap/snapshot?draft_id=draft-snap",
            headers={"x-shop-domain": "test-shop.myshopify.com"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["run_id"] == "run-snap"
    assert body["draft"]["draft_id"] == "draft-snap"
    assert [event["seq"] for event in body["events"]] == [1, 2]


@pytest.mark.asyncio
async def test_run_diagnostics_requires_shop_domain_header():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/agents/runs/run-diag/diagnostics")
    assert response.status_code == 400
    assert "shop_domain" in response.text


@pytest.mark.asyncio
async def test_run_diagnostics_includes_offload_and_retry_metadata(monkeypatch):
    ctx = get_app_context()
    captured: dict[str, object] = {}

    def fake_get_run_history(run_id: str, *, shop_domain=None):
        captured["history_shop_domain"] = shop_domain
        if run_id != "run-diag" or shop_domain != "test-shop.myshopify.com":
            return {"run": None, "events": [], "messages": []}
        return {
            "run": {
                "run_id": run_id,
                "status": "failed",
                "attempt": 3,
                "shop_domain": shop_domain,
                "resume_token": "resume-token-3",
                "failure_code": "import_failed",
                "failure_message": "Worker failed",
                "last_completed_step": "extract_products",
            },
            "events": [
                {"run_id": run_id, "seq": 1, "phase": "workflow_start"},
                {"run_id": run_id, "seq": 2, "phase": "offload_retry_scheduled"},
                {"run_id": run_id, "seq": 3, "phase": "workflow_failed"},
            ],
            "messages": [
                {"run_id": run_id, "seq": 1, "role": "system", "message": "start"}
            ],
        }

    def fake_list_offload_jobs_for_run(run_id: str, *, shop_domain=None, limit=20):
        captured["offload_query"] = {
            "run_id": run_id,
            "shop_domain": shop_domain,
            "limit": limit,
        }
        if run_id != "run-diag" or shop_domain != "test-shop.myshopify.com":
            return []
        return [
            {"job_id": "job-retry", "status": "retryable", "available_at": "2026-03-01T00:00:00+00:00"},
            {"job_id": "job-failed", "status": "failed"},
            {"job_id": "job-ok", "status": "succeeded"},
        ]

    monkeypatch.setattr(ctx.services.supabase, "get_run_history", fake_get_run_history)
    monkeypatch.setattr(
        ctx.services.supabase,
        "list_offload_jobs_for_run",
        fake_list_offload_jobs_for_run,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/agents/runs/run-diag/diagnostics?offload_limit=5&event_limit=10&message_limit=10",
            headers={"x-shop-domain": "TEST-SHOP.myshopify.com"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["run_id"] == "run-diag"
    assert [event["seq"] for event in body["history"]["events"]] == [1, 2, 3]
    assert [msg["seq"] for msg in body["history"]["messages"]] == [1]
    assert [job["job_id"] for job in body["offload_jobs"]] == [
        "job-retry",
        "job-failed",
        "job-ok",
    ]
    assert body["retry_diagnostics"] == {
        "run_attempt": 3,
        "run_status": "failed",
        "failure_code": "import_failed",
        "failure_message": "Worker failed",
        "last_completed_step": "extract_products",
        "resume_token_present": True,
        "retryable_offload_jobs": 1,
        "terminal_failed_offload_jobs": 1,
        "latest_retry_available_at": "2026-03-01T00:00:00+00:00",
    }
    assert captured == {
        "history_shop_domain": "test-shop.myshopify.com",
        "offload_query": {
            "run_id": "run-diag",
            "shop_domain": "test-shop.myshopify.com",
            "limit": 5,
        },
    }


@pytest.mark.asyncio
async def test_run_diagnostics_returns_404_for_other_tenant(monkeypatch):
    ctx = get_app_context()
    captured: dict[str, object] = {}

    def fake_get_run_history(run_id: str, *, shop_domain=None):
        captured["history_shop_domain"] = shop_domain
        return {"run": None, "events": [], "messages": []}

    monkeypatch.setattr(ctx.services.supabase, "get_run_history", fake_get_run_history)
    monkeypatch.setattr(
        ctx.services.supabase,
        "list_offload_jobs_for_run",
        lambda run_id, *, shop_domain=None, limit=20: [],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/agents/runs/run-diag/diagnostics",
            headers={"x-shop-domain": "other-shop.myshopify.com"},
        )

    assert response.status_code == 404
    assert captured == {"history_shop_domain": "other-shop.myshopify.com"}
