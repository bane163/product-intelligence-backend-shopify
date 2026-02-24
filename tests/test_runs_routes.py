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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resume_response = await ac.post(
            "/agents/runs/run-123/resume",
            data={"resume_token": "resume-token-1"},
            headers={"x-shop-domain": "test-shop.myshopify.com"},
        )
        assert resume_response.status_code == 200
        assert resume_response.json()["run"]["status"] == "queued"
        assert resume_response.json()["run"]["attempt"] == 2

        run_state.update({"status": "running"})
        cancel_response = await ac.post(
            "/agents/runs/run-123/cancel",
            headers={"x-shop-domain": "test-shop.myshopify.com"},
        )
        assert cancel_response.status_code == 200
        assert cancel_response.json()["run"]["status"] == "cancelled"

        run_state.update({"status": "failed"})
        retry_response = await ac.post(
            "/agents/runs/run-123/retry",
            headers={"x-shop-domain": "test-shop.myshopify.com"},
        )
        assert retry_response.status_code == 200
        assert retry_response.json()["run"]["status"] == "queued"
        assert retry_response.json()["run"]["attempt"] == 3
