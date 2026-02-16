from datetime import datetime, timezone
import os
import uuid

from app_context import AppContext


from services.interfaces import SupabaseServiceInterface, LLMServiceInterface, TracingServiceInterface


async def execute(
    supabase: SupabaseServiceInterface,
    llm: LLMServiceInterface,
    tracing: TracingServiceInterface,
    ctx: AppContext,
    file_bytes: bytes,
    input_name: str | None = None,
    input_content_type: str | None = None,
    run_id: str | None = None,
    prompt: str = "Please analyze the document and the associated image(s).",
    collabora_url: str | None = None,
    write_to_file: bool = False,
    output_path: str | None = None,
    writer_prompt: str | None = None,
    shop_domain: str | None = None,
):
    """Extracted application use-case for processing an uploaded document.

    This implementation mirrors the previous behavior from api/agents/files.process_excel
    but is isolated in the application layer. It currently uses AppContext directly for simplicity.
    """
    run_id = run_id or str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    emitter = None
    try:
        from application.services.run_event_emitter import RunEventEmitter

        emitter = RunEventEmitter(tracing=tracing, supabase=supabase, run_id=run_id)
        emit_and_persist = emitter.emit_and_persist
        trace_event = emitter.trace_event
    except Exception:
        # Fallback: simple no-op emitter
        def emit_and_persist(*args, **kwargs):
            return None

        def trace_event(*args, **kwargs):
            return None

    supabase.create_or_update_run(
        run_id,
        {
            "status": "running",
            "source": "document_import",
            "started_at": started_at.isoformat(),
            "prompt": prompt,
            "writer_prompt": writer_prompt,
        },
    )
    try:
        emit_and_persist(
            phase="request_received",
            message="Received /agents/import request",
            payload_preview={"write_to_file": write_to_file, "has_file_id": False},
        )

        # Persist input metadata
        supabase.create_or_update_run(
            run_id,
            {
                "input_file_id": None,
                "input_filename": input_name,
                "input_content_type": input_content_type,
                "input_size_bytes": len(file_bytes),
            },
        )

        model_env = None
        if shop_domain:
            active_model = supabase.get_active_llm_model_config(shop_domain)
            if active_model:
                model_env = {
                    "OLLAMA_CLOUD_URL": str(active_model.get("base_url") or ""),
                    "OLLAMA_MODEL_ID": str(active_model.get("model_id") or ""),
                    "OLLAMA_API_KEY": str(active_model.get("api_key") or ""),
                }
                supabase.create_or_update_run(
                    run_id,
                    {"model_name": active_model.get("model_id"), "provider": active_model.get("provider")},
                )
                emit_and_persist(
                    phase="model_config_selected",
                    message="Resolved active LLM config from database",
                    payload_preview={
                        "shop_domain": shop_domain,
                        "model_name": active_model.get("model_id"),
                        "provider": active_model.get("provider"),
                        "config_name": active_model.get("name"),
                    },
                )

        emit_and_persist(
            phase="workflow_start",
            message="Starting document workflow execution",
            payload_preview={"input_bytes": len(file_bytes)},
        )

        final_output_path = None
        if write_to_file:
            try:
                base_name = os.path.basename(input_name) if input_name else None
                if base_name:
                    name, ext = os.path.splitext(base_name)
                    suffixed = f"{name}-products{ext or ''}"
                    final_output_path = os.path.abspath(os.path.join(os.getcwd(), suffixed))
                elif output_path:
                    final_output_path = os.path.abspath(output_path)
                else:
                    final_output_path = os.path.abspath(os.path.join(os.getcwd(), "import-products.xlsx"))
            except Exception:
                final_output_path = output_path

        result = await llm.run_excel_agent_workflow(
            file_bytes,
            collabora_base_url=collabora_url,
            agent_prompt=prompt,
            model_env=model_env,
            write_to_file=write_to_file,
            output_path=final_output_path,
            writer_agent_prompt=writer_prompt,
            trace_event=trace_event,
        )
        emit_and_persist(phase="workflow_done", message="Document workflow completed")
    except Exception as exc:
        emit_and_persist(
            phase="workflow_error",
            message="Document workflow failed",
            level="error",
            error=str(exc),
        )
        duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        try:
            supabase.finalize_run(run_id, status="error", duration_ms=duration_ms, error=str(exc))
        except Exception:
            pass
        try:
            tracing.complete_run(run_id)
        except Exception:
            pass
        raise

    # If writer persisted file to disk, handle saving it
    generated_file_id = None
    generated_filename = None
    if write_to_file:
        if isinstance(result, dict) and result.get("file_id"):
            pass
        elif isinstance(result, str):
            try:
                if os.path.exists(result):
                    with open(result, "rb") as fh:
                        out_bytes = fh.read()
                    generated_file_id = str(uuid.uuid4())
                    generated_filename = os.path.basename(result)
                    ct = (
                        "text/csv" if generated_filename.lower().endswith(".csv") else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    supabase.save_file(generated_file_id, name=generated_filename, content=out_bytes, content_type=ct)
                    try:
                        os.remove(result)
                    except Exception:
                        pass
                    result = {"workbook_path": result, "file_id": generated_file_id, "filename": generated_filename}
            except Exception:
                emit_and_persist(
                    phase="storage_upload_error",
                    message="Failed to persist generated workbook to storage",
                    level="error",
                    error=str(Exception),
                )

    # Cleanup input file id if present
    try:
        # No input file id stored by this use-case; caller handles deletion when appropriate.
        pass
    except Exception:
        pass

    output_meta = {}
    if isinstance(result, dict):
        output_meta = {
            "output_file_id": result.get("file_id"),
            "output_filename": result.get("filename"),
        }
    duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    try:
        supabase.finalize_run(run_id, status="success", duration_ms=duration_ms, extra_fields=output_meta)
    except Exception:
        pass
    try:
        tracing.complete_run(run_id)
    except Exception:
        pass

    return {"run_id": run_id, "result": result}