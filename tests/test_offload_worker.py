import logging
from datetime import datetime, timedelta, timezone

import pytest

from ai.models import ProductsList
from app_context import AppContext, ServiceRegistry
from infrastructure.adapters.supabase_adapter import SupabaseAdapter
from services.supabase_service import SupabaseService
from services.tracing_service import TracingService
from application.services.offload_worker import _failure_code, _is_retryable_job_failure


def test_corrupt_document_failures_are_not_retryable():
    assert not _is_retryable_job_failure(
        job_type="document_import",
        error_message="Invalid file content: File is not a zip file",
    )


def test_source_evidence_validation_failures_are_not_retryable():
    assert not _is_retryable_job_failure(
        job_type="document_import",
        error_message="SOURCE_EVIDENCE_VALIDATION_FAILED: title needs an evidence_index",
    )


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("COPILOT_PERMISSION_DENIED: execute_code", "COPILOT_PERMISSION_DENIED"),
        ("Copilot unavailable tool: execute_code", "COPILOT_TOOL_UNAVAILABLE"),
        ("COPILOT_EXTRACTION_INVALID: invalid payload", "COPILOT_INVALID_PAYLOAD"),
    ],
)
def test_copilot_contract_failures_are_terminal(message, code):
    assert not _is_retryable_job_failure(job_type="document_import", error_message=message)
    assert _failure_code(message) == code


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("Provider unavailable: status 503", "COPILOT_PROVIDER_UNAVAILABLE"),
        ("Rate limit exceeded (HTTP 429)", "COPILOT_RATE_LIMITED"),
        ("Network error: connection reset", "collabora_unavailable"),
        ("Copilot request timed out", "COPILOT_PROVIDER_TIMEOUT"),
        ("Collabora service unavailable", "collabora_unavailable"),
    ],
)
def test_recognized_transient_document_failures_remain_retryable(message, code):
    assert _is_retryable_job_failure(job_type="document_import", error_message=message)
    assert _failure_code(message) == code


def test_unrecognized_document_failure_is_terminal():
    assert not _is_retryable_job_failure(
        job_type="document_import",
        error_message="Unexpected malformed worker state",
    )


class _DummyLLM:
    pass


class _DummyCollabora:
    pass


class _DummyShopify:
    pass


def _build_ctx() -> AppContext:
    supabase_service = SupabaseService()
    supabase_service._get_supabase_client = lambda: None  # type: ignore[attr-defined]
    supabase_service._try_get_bucket = lambda: None  # type: ignore[attr-defined]
    return AppContext(
        services=ServiceRegistry(
            supabase=SupabaseAdapter(supabase_service),
            llm=_DummyLLM(),
            collabora=_DummyCollabora(),
            tracing=TracingService(),
            shopify=_DummyShopify(),
        )
    )


def _seed_approved_submit_plan(
    ctx: AppContext, *, draft_id: str, products: list[dict]
) -> tuple[str, int]:
    draft = ctx.supabase.drafts.get_product_draft(draft_id)
    assert isinstance(draft, dict)
    revision = int(draft.get("revision") or 1)
    plan_id = f"plan-{draft_id}"
    ctx.supabase._service.save_mutation_plan(
        {
            "plan_id": plan_id,
            "draft_id": draft_id,
            "shop_domain": draft.get("shop_domain") or "worker-test.myshopify.com",
            "draft_revision": revision,
            "version": 1,
            "status": "approved",
            "items": [
                {
                    "item_id": f"item-{index}",
                    "selected": True,
                    "selectable": True,
                    "blockers": [],
                    "proposed": product,
                }
                for index, product in enumerate(products)
            ],
        }
    )
    return plan_id, revision


def test_claim_next_offload_job_claims_retryable_job_when_available():
    ctx = _build_ctx()
    job_id = "job-claim-retryable"
    ctx.supabase.runs.enqueue_offload_job(
        job_id,
        {
            "queue_name": "offload",
            "job_type": "document_import",
            "status": "retryable",
            "run_id": "run-claim-retryable",
            "draft_id": "draft-claim-retryable",
            "file_id": "file-claim-retryable",
            "attempt_count": 1,
            "max_attempts": 3,
            "available_at": (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat(),
            "payload": {},
        },
    )

    claimed = ctx.supabase.runs.claim_next_offload_job(
        queue_name="offload",
        worker_id="worker-1",
        lease_seconds=60,
    )

    assert claimed is not None
    assert claimed["job_id"] == job_id
    assert claimed["status"] == "claimed"
    assert int(claimed.get("attempt_count") or 0) == 2


def test_create_once_enqueue_does_not_reset_existing_job():
    ctx = _build_ctx()
    job_id = "deterministic-submit-job"
    original = ctx.supabase.runs.enqueue_offload_job(
        job_id,
        {
            "queue_name": "offload",
            "job_type": "shopify_submit",
            "status": "queued",
            "run_id": "run-original",
            "payload": {"plan_id": "plan-original"},
        },
        create_once=True,
    )
    assert original is not None
    ctx.supabase.runs.update_offload_job(job_id, {"status": "running"})

    replay = ctx.supabase.runs.enqueue_offload_job(
        job_id,
        {
            "queue_name": "offload",
            "job_type": "shopify_submit",
            "status": "queued",
            "run_id": "run-replacement",
            "payload": {"plan_id": "plan-replacement"},
        },
        create_once=True,
    )

    assert replay is not None
    assert replay["status"] == "running"
    assert replay["run_id"] == "run-original"
    assert replay["payload"] == {"plan_id": "plan-original"}


@pytest.mark.asyncio
async def test_run_once_processes_document_import_job_success(monkeypatch):
    import application.services.offload_worker as offload_worker

    ctx = _build_ctx()
    run_id = "run-document-success"
    draft_id = "draft-document-success"
    file_id = "file-document-success"
    job_id = "job-document-success"
    ctx.supabase.file.save_file(
        file_id,
        name="queued.xlsx",
        content=b"queued",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    ctx.supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=run_id,
        import_mode="auto",
        draft_name="Queued Draft",
        input_file_id=file_id,
        input_filename="queued.xlsx",
        extraction_status="queued",
        extraction_run_id=run_id,
        extraction_error=None,
        submit_status=None,
        submit_run_id=None,
        submit_error=None,
        products=[],
    )
    ctx.supabase.runs.enqueue_offload_job(
        job_id,
        {
            "queue_name": "offload",
            "job_type": "document_import",
            "status": "queued",
            "run_id": run_id,
            "draft_id": draft_id,
            "file_id": file_id,
            "payload": {
                "input_filename": "queued.xlsx",
                "input_content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "extraction_mode": "per_sheet",
                "enable_ai_enhancements": False,
            },
        },
    )

    async def fake_process_document_execute(**kwargs):
        assert kwargs["run_id"] == run_id
        assert kwargs["file_bytes"] == b"queued"
        assert kwargs["enable_ai_enhancements"] is False
        return {
            "run_id": run_id,
            "result": {
                "products": [{"title": "Queued Product"}],
            },
        }

    monkeypatch.setattr(
        offload_worker,
        "process_document_execute",
        fake_process_document_execute,
    )

    worker = offload_worker.OffloadWorker(
        ctx=ctx, queue_name="offload", worker_id="worker-1"
    )
    processed = await worker.run_once()
    assert processed is True

    job = ctx.supabase.runs.get_offload_job(job_id)
    assert job is not None
    assert job["status"] == "succeeded"
    assert job["result"]["run_id"] == run_id
    assert job["result"]["draft_id"] == draft_id
    assert job["result"]["product_count"] == 1

    draft = ctx.supabase.drafts.get_product_draft(draft_id)
    assert draft is not None
    assert draft["ai_enhancements_enabled"] is False
    assert draft["extraction_status"] == "succeeded"
    assert draft["extraction_run_id"] == run_id
    assert "output_file_id" not in draft
    assert "output_filename" not in draft
    assert len(draft["products"]) == 1


@pytest.mark.asyncio
async def test_run_once_processes_document_import_products_list_payload(monkeypatch):
    import application.services.offload_worker as offload_worker

    ctx = _build_ctx()
    run_id = "run-document-products-list"
    draft_id = "draft-document-products-list"
    file_id = "file-document-products-list"
    job_id = "job-document-products-list"
    ctx.supabase.file.save_file(
        file_id,
        name="queued-products-list.xlsx",
        content=b"queued",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    ctx.supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=run_id,
        import_mode="auto",
        draft_name="Queued Draft",
        input_file_id=file_id,
        input_filename="queued-products-list.xlsx",
        extraction_status="queued",
        extraction_run_id=run_id,
        extraction_error=None,
        submit_status=None,
        submit_run_id=None,
        submit_error=None,
        products=[],
    )
    ctx.supabase.runs.enqueue_offload_job(
        job_id,
        {
            "queue_name": "offload",
            "job_type": "document_import",
            "status": "queued",
            "run_id": run_id,
            "draft_id": draft_id,
            "file_id": file_id,
            "payload": {
                "input_filename": "queued-products-list.xlsx",
                "input_content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "extraction_mode": "per_sheet",
            },
        },
    )

    async def fake_process_document_execute(**kwargs):
        assert kwargs["run_id"] == run_id
        assert kwargs["file_bytes"] == b"queued"
        return {
            "run_id": run_id,
            "result": ProductsList(products=[{"title": "Queued Product"}]),
        }

    monkeypatch.setattr(
        offload_worker,
        "process_document_execute",
        fake_process_document_execute,
    )

    worker = offload_worker.OffloadWorker(
        ctx=ctx, queue_name="offload", worker_id="worker-1"
    )
    processed = await worker.run_once()
    assert processed is True

    job = ctx.supabase.runs.get_offload_job(job_id)
    assert job is not None
    assert job["status"] == "succeeded"
    assert job["result"]["run_id"] == run_id
    assert job["result"]["draft_id"] == draft_id
    assert job["result"]["product_count"] == 1

    draft = ctx.supabase.drafts.get_product_draft(draft_id)
    assert draft is not None
    assert draft["extraction_status"] == "succeeded"
    assert draft["extraction_run_id"] == run_id
    assert len(draft["products"]) == 1
    assert draft["products"][0]["title"] == "Queued Product"


@pytest.mark.asyncio
async def test_run_once_marks_document_import_job_failed_when_file_missing():
    import application.services.offload_worker as offload_worker

    ctx = _build_ctx()
    run_id = "run-document-failed"
    draft_id = "draft-document-failed"
    job_id = "job-document-failed"
    ctx.supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=run_id,
        import_mode="auto",
        draft_name="Queued Draft",
        input_file_id="missing-file",
        input_filename="missing.xlsx",
        extraction_status="queued",
        extraction_run_id=run_id,
        extraction_error=None,
        submit_status=None,
        submit_run_id=None,
        submit_error=None,
        products=[],
    )
    ctx.supabase.runs.enqueue_offload_job(
        job_id,
        {
            "queue_name": "offload",
            "job_type": "document_import",
            "status": "queued",
            "run_id": run_id,
            "draft_id": draft_id,
            "file_id": "missing-file",
            "payload": {},
        },
    )

    worker = offload_worker.OffloadWorker(
        ctx=ctx, queue_name="offload", worker_id="worker-1"
    )
    processed = await worker.run_once()
    assert processed is True

    job = ctx.supabase.runs.get_offload_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert "file not found" in str(job.get("error", "")).lower()

    draft = ctx.supabase.drafts.get_product_draft(draft_id)
    assert draft is not None
    assert draft["extraction_status"] == "failed"
    assert "file not found" in str(draft.get("extraction_error", "")).lower()


@pytest.mark.asyncio
async def test_run_once_requeues_document_import_job_before_max_attempts(monkeypatch):
    import application.services.offload_worker as offload_worker

    ctx = _build_ctx()
    run_id = "run-document-retryable"
    draft_id = "draft-document-retryable"
    file_id = "file-document-retryable"
    job_id = "job-document-retryable"
    ctx.supabase.file.save_file(
        file_id,
        name="queued-retryable.xlsx",
        content=b"queued",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    ctx.supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=run_id,
        import_mode="auto",
        draft_name="Queued Retryable",
        input_file_id=file_id,
        input_filename="queued-retryable.xlsx",
        extraction_status="queued",
        extraction_run_id=run_id,
        extraction_error=None,
        submit_status=None,
        submit_run_id=None,
        submit_error=None,
        products=[],
    )
    ctx.supabase.runs.enqueue_offload_job(
        job_id,
        {
            "queue_name": "offload",
            "job_type": "document_import",
            "status": "queued",
            "run_id": run_id,
            "draft_id": draft_id,
            "file_id": file_id,
            "max_attempts": 3,
            "payload": {
                "input_filename": "queued-retryable.xlsx",
                "input_content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "extraction_mode": "per_sheet",
            },
        },
    )

    async def fake_process_document_execute(**kwargs):
        _ = kwargs
        raise RuntimeError("provider temporarily unavailable")

    monkeypatch.setattr(
        offload_worker,
        "process_document_execute",
        fake_process_document_execute,
    )

    worker = offload_worker.OffloadWorker(
        ctx=ctx, queue_name="offload", worker_id="worker-1"
    )
    processed = await worker.run_once()
    assert processed is True

    job = ctx.supabase.runs.get_offload_job(job_id)
    assert job is not None
    assert job["status"] == "retryable"
    assert "provider temporarily unavailable" in str(job.get("error", "")).lower()
    assert int(job.get("attempt_count") or 0) == 1
    available_at = datetime.fromisoformat(
        str(job.get("available_at")).replace("Z", "+00:00")
    )
    assert available_at > datetime.now(timezone.utc)
    result_payload = job.get("result")
    assert isinstance(result_payload, dict)
    assert result_payload.get("dead_letter") is False

    draft = ctx.supabase.drafts.get_product_draft(draft_id)
    assert draft is not None
    assert draft["extraction_status"] == "running"
    assert draft["extraction_error"] is None

    run = ctx.supabase.runs.get_run(run_id)
    assert run is None or run["status"] != "failed"


@pytest.mark.asyncio
async def test_run_once_marks_document_import_job_dead_letter_after_max_attempts(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
):
    import application.services.offload_worker as offload_worker

    ctx = _build_ctx()
    run_id = "run-document-dead-letter"
    draft_id = "draft-document-dead-letter"
    file_id = "file-document-dead-letter"
    job_id = "job-document-dead-letter"
    ctx.supabase.file.save_file(
        file_id,
        name="queued-dead-letter.xlsx",
        content=b"queued",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    ctx.supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=run_id,
        import_mode="auto",
        draft_name="Queued Dead Letter",
        input_file_id=file_id,
        input_filename="queued-dead-letter.xlsx",
        extraction_status="queued",
        extraction_run_id=run_id,
        extraction_error=None,
        submit_status=None,
        submit_run_id=None,
        submit_error=None,
        products=[],
    )
    ctx.supabase.runs.create_or_update_run(
        run_id,
        {
            "status": "queued",
            "source": "document_import",
        },
    )
    ctx.supabase.runs.enqueue_offload_job(
        job_id,
        {
            "queue_name": "offload",
            "job_type": "document_import",
            "status": "queued",
            "run_id": run_id,
            "draft_id": draft_id,
            "file_id": file_id,
            "max_attempts": 1,
            "payload": {
                "input_filename": "queued-dead-letter.xlsx",
                "input_content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "extraction_mode": "per_sheet",
            },
        },
    )

    async def fake_process_document_execute(**kwargs):
        _ = kwargs
        raise RuntimeError("permanent extraction failure")

    monkeypatch.setattr(
        offload_worker,
        "process_document_execute",
        fake_process_document_execute,
    )

    worker = offload_worker.OffloadWorker(
        ctx=ctx, queue_name="offload", worker_id="worker-1"
    )
    caplog.set_level(logging.INFO, logger="metrics.signals")
    processed = await worker.run_once()
    assert processed is True

    job = ctx.supabase.runs.get_offload_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert "permanent extraction failure" in str(job.get("error", "")).lower()
    result_payload = job.get("result")
    assert isinstance(result_payload, dict)
    assert result_payload.get("dead_letter") is True

    draft = ctx.supabase.drafts.get_product_draft(draft_id)
    assert draft is not None
    assert draft["extraction_status"] == "failed"
    assert (
        "permanent extraction failure" in str(draft.get("extraction_error", "")).lower()
    )

    run = ctx.supabase.runs.get_run(run_id)
    assert run is None or run["status"] == "failed"
    signal_messages = [record.getMessage() for record in caplog.records]
    assert any(
        '"event": "offload.worker_job"' in message and '"dead_letter": true' in message
        for message in signal_messages
    )


@pytest.mark.asyncio
async def test_run_once_document_import_auto_submit_stops_for_merchant_review(
    monkeypatch,
):
    import application.services.offload_worker as offload_worker

    ctx = _build_ctx()
    extraction_run_id = "run-document-auto-submit"
    submit_run_id = "run-submit-auto-submit"
    draft_id = "draft-document-auto-submit"
    file_id = "file-document-auto-submit"
    job_id = "job-document-auto-submit"
    ctx.supabase.file.save_file(
        file_id,
        name="queued-auto-submit.xlsx",
        content=b"queued",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    ctx.supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=extraction_run_id,
        import_mode="auto",
        draft_name="Queued Auto Submit",
        input_file_id=file_id,
        input_filename="queued-auto-submit.xlsx",
        extraction_status="queued",
        extraction_run_id=extraction_run_id,
        extraction_error=None,
        submit_status=None,
        submit_run_id=submit_run_id,
        submit_error=None,
        products=[],
    )
    ctx.supabase.runs.enqueue_offload_job(
        job_id,
        {
            "queue_name": "offload",
            "job_type": "document_import",
            "status": "queued",
            "run_id": extraction_run_id,
            "draft_id": draft_id,
            "file_id": file_id,
            "payload": {
                "input_filename": "queued-auto-submit.xlsx",
                "input_content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "extraction_mode": "per_sheet",
                "auto_submit": True,
                "submit_run_id": submit_run_id,
                "import_mode": "auto",
            },
        },
    )

    async def fake_process_document_execute(**kwargs):
        assert kwargs["run_id"] == extraction_run_id
        assert kwargs["file_bytes"] == b"queued"
        return {
            "run_id": extraction_run_id,
            "result": {
                "products": [{"title": "Queued Product"}],
            },
        }

    monkeypatch.setattr(
        offload_worker,
        "process_document_execute",
        fake_process_document_execute,
    )

    worker = offload_worker.OffloadWorker(
        ctx=ctx, queue_name="offload", worker_id="worker-1"
    )
    processed = await worker.run_once()
    assert processed is True

    import_job = ctx.supabase.runs.get_offload_job(job_id)
    assert import_job is not None
    assert import_job["status"] == "succeeded"

    offload_jobs = getattr(ctx.supabase._service, "offload_jobs", {})
    submit_jobs = [
        item
        for item in offload_jobs.values()
        if item.get("job_type") == "shopify_submit" and item.get("draft_id") == draft_id
    ]
    assert submit_jobs == []

    submit_run = ctx.supabase.runs.get_run(submit_run_id)
    assert submit_run is None

    draft = ctx.supabase.drafts.get_product_draft(draft_id)
    assert draft is not None
    assert draft["quality_gate_status"] == "live_validation_pending"
    assert draft["submit_status"] is None
    assert draft["submit_run_id"] == submit_run_id


@pytest.mark.asyncio
async def test_run_once_processes_shopify_submit_job_success(monkeypatch):
    import application.services.offload_worker as offload_worker
    import shopify_session_store

    ctx = _build_ctx()
    run_id = "run-submit-success"
    draft_id = "draft-submit-success"
    job_id = "job-submit-success"
    ctx.supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=run_id,
        import_mode="auto",
        draft_name="Queued Submit",
        input_file_id=None,
        input_filename=None,
        extraction_status="succeeded",
        extraction_run_id="run-extract",
        extraction_error=None,
        submit_status="queued",
        submit_run_id=run_id,
        submit_error=None,
        ai_enhancements_enabled=False,
        products=[{"title": "Queued Product"}],
        shop_domain="worker-test.myshopify.com",
    )
    plan_id, revision = _seed_approved_submit_plan(
        ctx, draft_id=draft_id, products=[{"title": "Queued Product"}]
    )
    monkeypatch.setattr(
        shopify_session_store, "get_offline_access_token", lambda shop: "token"
    )
    ctx.supabase.runs.enqueue_offload_job(
        job_id,
        {
            "queue_name": "offload",
            "job_type": "shopify_submit",
            "status": "queued",
            "run_id": run_id,
            "draft_id": draft_id,
            "shop_domain": "worker-test.myshopify.com",
            "payload": {
                "import_mode": "auto",
                "document_name": "Queued Submit",
                "enable_ai_enhancements": False,
                "plan_id": plan_id,
                "revision": revision,
            },
        },
    )

    async def fake_submit_execute(**kwargs):
        assert kwargs["run_id"] == run_id
        assert kwargs["draft_id"] == draft_id
        assert kwargs["ai_enhancements_enabled"] is False
        return {
            "submitted_id": "submitted-123",
            "success_count": 1,
            "failed_count": 0,
            "partial_count": 0,
            "results": [{"status": "success"}],
        }

    monkeypatch.setattr(
        offload_worker,
        "submit_execute",
        fake_submit_execute,
    )

    worker = offload_worker.OffloadWorker(
        ctx=ctx, queue_name="offload", worker_id="worker-1"
    )
    processed = await worker.run_once()
    assert processed is True

    job = ctx.supabase.runs.get_offload_job(job_id)
    assert job is not None
    assert job["status"] == "succeeded"
    assert job["result"]["run_id"] == run_id
    assert job["result"]["draft_id"] == draft_id
    assert job["result"]["submitted_id"] == "submitted-123"
    assert job["result"]["success_count"] == 1

    draft = ctx.supabase.drafts.get_product_draft(draft_id)
    assert draft is not None
    assert draft["submit_status"] == "succeeded"
    assert draft["submit_run_id"] == run_id
    assert draft["submit_error"] is None


@pytest.mark.asyncio
async def test_run_once_marks_shopify_submit_job_failed(monkeypatch):
    import application.services.offload_worker as offload_worker
    import shopify_session_store

    ctx = _build_ctx()
    run_id = "run-submit-failed"
    draft_id = "draft-submit-failed"
    job_id = "job-submit-failed"
    ctx.supabase.runs.create_or_update_run(
        run_id,
        {
            "status": "queued",
            "source": "shopify_submit",
        },
    )
    ctx.supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=run_id,
        import_mode="auto",
        draft_name="Queued Submit",
        input_file_id=None,
        input_filename=None,
        extraction_status="succeeded",
        extraction_run_id="run-extract",
        extraction_error=None,
        submit_status="queued",
        submit_run_id=run_id,
        submit_error=None,
        products=[{"title": "Queued Product"}],
        shop_domain="worker-test.myshopify.com",
    )
    plan_id, revision = _seed_approved_submit_plan(
        ctx, draft_id=draft_id, products=[{"title": "Queued Product"}]
    )
    monkeypatch.setattr(
        shopify_session_store, "get_offline_access_token", lambda shop: "token"
    )
    ctx.supabase.runs.enqueue_offload_job(
        job_id,
        {
            "queue_name": "offload",
            "job_type": "shopify_submit",
            "status": "queued",
            "run_id": run_id,
            "draft_id": draft_id,
            "shop_domain": "worker-test.myshopify.com",
            "payload": {
                "import_mode": "auto",
                "plan_id": plan_id,
                "revision": revision,
            },
        },
    )

    async def fake_submit_execute(**kwargs):
        _ = kwargs
        raise RuntimeError("submit explosion")

    monkeypatch.setattr(
        offload_worker,
        "submit_execute",
        fake_submit_execute,
    )

    worker = offload_worker.OffloadWorker(
        ctx=ctx, queue_name="offload", worker_id="worker-1"
    )
    processed = await worker.run_once()
    assert processed is True

    job = ctx.supabase.runs.get_offload_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert "submit explosion" in str(job.get("error", "")).lower()

    draft = ctx.supabase.drafts.get_product_draft(draft_id)
    assert draft is not None
    assert draft["submit_status"] == "failed"
    assert "submit explosion" in str(draft.get("submit_error", "")).lower()

    run = ctx.supabase.runs.get_run(run_id)
    assert run is None or run["status"] == "failed"


@pytest.mark.asyncio
async def test_run_once_shopify_submit_failed_uses_count_based_error(monkeypatch):
    import application.services.offload_worker as offload_worker
    import shopify_session_store

    ctx = _build_ctx()
    run_id = "run-submit-count-failed"
    draft_id = "draft-submit-count-failed"
    job_id = "job-submit-count-failed"
    ctx.supabase.runs.create_or_update_run(
        run_id,
        {
            "status": "queued",
            "source": "shopify_submit",
        },
    )
    ctx.supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=run_id,
        import_mode="auto",
        draft_name="Queued Submit",
        input_file_id=None,
        input_filename=None,
        extraction_status="succeeded",
        extraction_run_id="run-extract",
        extraction_error=None,
        submit_status="queued",
        submit_run_id=run_id,
        submit_error=None,
        products=[{"title": "Queued Product 1"}, {"title": "Queued Product 2"}],
        shop_domain="worker-test.myshopify.com",
    )
    proposed = [{"title": "Queued Product 1"}, {"title": "Queued Product 2"}]
    plan_id, revision = _seed_approved_submit_plan(
        ctx, draft_id=draft_id, products=proposed
    )
    monkeypatch.setattr(
        shopify_session_store, "get_offline_access_token", lambda shop: "token"
    )
    ctx.supabase.runs.enqueue_offload_job(
        job_id,
        {
            "queue_name": "offload",
            "job_type": "shopify_submit",
            "status": "queued",
            "run_id": run_id,
            "draft_id": draft_id,
            "shop_domain": "worker-test.myshopify.com",
            "payload": {
                "import_mode": "auto",
                "plan_id": plan_id,
                "revision": revision,
            },
        },
    )

    async def fake_submit_execute(**kwargs):
        _ = kwargs
        return {
            "submitted_id": None,
            "success_count": 0,
            "failed_count": 2,
            "partial_count": 0,
            "results": [
                {"status": "failed", "errors": []},
                {"status": "failed", "errors": []},
            ],
            "error": None,
        }

    monkeypatch.setattr(
        offload_worker,
        "submit_execute",
        fake_submit_execute,
    )

    worker = offload_worker.OffloadWorker(
        ctx=ctx, queue_name="offload", worker_id="worker-1"
    )
    processed = await worker.run_once()
    assert processed is True

    job = ctx.supabase.runs.get_offload_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert "success_count=0" in str(job.get("error", "")).lower()
    assert "failed_count=2" in str(job.get("error", "")).lower()

    draft = ctx.supabase.drafts.get_product_draft(draft_id)
    assert draft is not None
    assert draft["submit_status"] == "failed"
    assert "success_count=0" in str(draft.get("submit_error", "")).lower()


@pytest.mark.asyncio
async def test_run_once_fails_fast_when_draft_lifecycle_persistence_fails(monkeypatch):
    import application.services.offload_worker as offload_worker

    ctx = _build_ctx()
    run_id = "run-document-lifecycle-fail"
    draft_id = "draft-document-lifecycle-fail"
    file_id = "file-document-lifecycle-fail"
    job_id = "job-document-lifecycle-fail"
    ctx.supabase.file.save_file(
        file_id,
        name="queued.xlsx",
        content=b"queued",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    ctx.supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=run_id,
        import_mode="auto",
        draft_name="Queued Draft",
        input_file_id=file_id,
        input_filename="queued.xlsx",
        extraction_status="queued",
        extraction_run_id=run_id,
        extraction_error=None,
        submit_status=None,
        submit_run_id=None,
        submit_error=None,
        products=[],
    )
    ctx.supabase.runs.enqueue_offload_job(
        job_id,
        {
            "queue_name": "offload",
            "job_type": "document_import",
            "status": "queued",
            "run_id": run_id,
            "draft_id": draft_id,
            "file_id": file_id,
            "payload": {},
        },
    )

    original_save_product_draft = ctx.services.supabase.save_product_draft

    def fake_save_product_draft(**kwargs):
        if kwargs.get("require_lifecycle_columns"):
            raise RuntimeError("product_drafts lifecycle columns missing")
        return original_save_product_draft(**kwargs)

    monkeypatch.setattr(
        ctx.services.supabase,
        "save_product_draft",
        fake_save_product_draft,
        raising=False,
    )

    worker = offload_worker.OffloadWorker(
        ctx=ctx, queue_name="offload", worker_id="worker-1"
    )
    processed = await worker.run_once()
    assert processed is True

    job = ctx.supabase.runs.get_offload_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert "lifecycle columns missing" in str(job.get("error", "")).lower()
