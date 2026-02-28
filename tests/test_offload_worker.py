import pytest

from app_context import AppContext, ServiceRegistry
from infrastructure.adapters.supabase_adapter import SupabaseAdapter
from services.supabase_service import SupabaseService
from services.tracing_service import TracingService


class _DummyLLM:
    pass


class _DummyCollabora:
    pass


class _DummyShopify:
    pass


def _build_ctx() -> AppContext:
    supabase_service = SupabaseService()
    supabase_service._get_supabase_client = lambda: None  # type: ignore[attr-defined]
    return AppContext(
        services=ServiceRegistry(
            supabase=SupabaseAdapter(supabase_service),
            llm=_DummyLLM(),
            collabora=_DummyCollabora(),
            tracing=TracingService(),
            shopify=_DummyShopify(),
        )
    )


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
            },
        },
    )

    async def fake_process_document_execute(**kwargs):
        assert kwargs["run_id"] == run_id
        assert kwargs["file_bytes"] == b"queued"
        return {
            "run_id": run_id,
            "result": {
                "products": [{"title": "Queued Product"}],
                "file_id": "output-file",
                "filename": "output.xlsx",
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
    assert draft["extraction_status"] == "succeeded"
    assert draft["extraction_run_id"] == run_id
    assert draft["output_file_id"] == "output-file"
    assert draft["output_filename"] == "output.xlsx"
    assert len(draft["products"]) == 1


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
async def test_run_once_processes_shopify_submit_job_success(monkeypatch):
    import application.services.offload_worker as offload_worker

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
        products=[{"title": "Queued Product"}],
    )
    ctx.supabase.runs.enqueue_offload_job(
        job_id,
        {
            "queue_name": "offload",
            "job_type": "shopify_submit",
            "status": "queued",
            "run_id": run_id,
            "draft_id": draft_id,
            "payload": {
                "import_mode": "auto",
                "document_name": "Queued Submit",
                "enable_ai_enhancements": False,
            },
        },
    )

    async def fake_submit_execute(**kwargs):
        assert kwargs["run_id"] == run_id
        assert kwargs["draft_id"] == draft_id
        return {
            "submitted_id": "submitted-123",
            "success_count": 1,
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
    )
    ctx.supabase.runs.enqueue_offload_job(
        job_id,
        {
            "queue_name": "offload",
            "job_type": "shopify_submit",
            "status": "queued",
            "run_id": run_id,
            "draft_id": draft_id,
            "payload": {"import_mode": "auto"},
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
