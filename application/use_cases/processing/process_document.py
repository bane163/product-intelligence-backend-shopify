from datetime import datetime, timezone
import os
from time import perf_counter
from typing import Any, Callable
import uuid

from app_context import AppContext

from application.domain.product_intelligence_patching import (
    VARIANT_OPERATIONS_FIELD,
    apply_suggestions_to_products,
)
from application.ports.llm_port import LLMPort
from application.ports.supabase_port import SupabaseNamespacedPort
from application.ports.tracing_port import TracingPort
from application.use_cases.intelligence_generate_suggestions import (
    execute as generate_suggestions_execute,
)

DEFAULT_IMPORT_AGENT_PROMPT = "Please analyze the document and the associated image(s)."
DEFAULT_IMPORT_WRITER_PROMPT: str | None = None


async def execute(
    supabase: SupabaseNamespacedPort,
    llm: LLMPort,
    tracing: TracingPort,
    ctx: AppContext,
    file_bytes: bytes,
    input_name: str | None = None,
    input_content_type: str | None = None,
    run_id: str | None = None,
    collabora_url: str | None = None,
    extraction_mode: str = "per_sheet",
    write_to_file: bool = False,
    output_path: str | None = None,
    shop_domain: str | None = None,
):
    """Extracted application use-case for processing an uploaded document.

    This implementation mirrors the previous behavior from api/agents/files.process_excel
    but is isolated in the application layer. It currently uses AppContext directly for simplicity.
    """
    run_id = run_id or str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    emitter = None
    emit_and_persist: Callable[..., Any]
    trace_event: Callable[..., Any]
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

    supabase.runs.create_or_update_run(
        run_id,
        {
            "status": "queued",
            "source": "document_import",
            "started_at": started_at.isoformat(),
            "prompt": DEFAULT_IMPORT_AGENT_PROMPT,
            "writer_prompt": DEFAULT_IMPORT_WRITER_PROMPT,
            "attempt": 1,
            "shop_domain": shop_domain,
        },
    )
    try:
        emit_and_persist(
            phase="request_received",
            message="Received /agents/import request",
            payload_preview={
                "write_to_file": write_to_file,
                "has_file_id": False,
                "extraction_mode": extraction_mode,
            },
        )

        # Persist input metadata
        supabase.runs.create_or_update_run(
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
            active_model = supabase.llm_configs.get_active_llm_model_config(shop_domain)
            if active_model:
                model_env = {
                    "OLLAMA_CLOUD_URL": str(active_model.get("base_url") or ""),
                    "OLLAMA_MODEL_ID": str(active_model.get("model_id") or ""),
                    "OLLAMA_API_KEY": str(active_model.get("api_key") or ""),
                }
                supabase.runs.create_or_update_run(
                    run_id,
                    {
                        "model_name": active_model.get("model_id"),
                        "provider": active_model.get("provider"),
                    },
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
        supabase.runs.create_or_update_run(
            run_id,
            {
                "status": "running",
                "shop_domain": shop_domain,
            },
        )

        final_output_path = None
        if write_to_file:
            try:
                base_name = os.path.basename(input_name) if input_name else None
                if base_name:
                    name, ext = os.path.splitext(base_name)
                    suffixed = f"{name}-products{ext or ''}"
                    final_output_path = os.path.abspath(
                        os.path.join(os.getcwd(), suffixed)
                    )
                elif output_path:
                    final_output_path = os.path.abspath(output_path)
                else:
                    final_output_path = os.path.abspath(
                        os.path.join(os.getcwd(), "import-products.xlsx")
                    )
            except Exception:
                final_output_path = output_path

        result = await llm.run_excel_agent_workflow(
            file_bytes,
            collabora_base_url=collabora_url,
            agent_prompt=DEFAULT_IMPORT_AGENT_PROMPT,
            model_env=model_env,
            input_name=input_name,
            input_content_type=input_content_type,
            extraction_mode=extraction_mode,
            write_to_file=write_to_file,
            output_path=final_output_path,
            writer_agent_prompt=DEFAULT_IMPORT_WRITER_PROMPT,
            trace_event=trace_event,
        )
        if isinstance(result, dict):
            enrichment_attempted = False
            enrichment_applied = False
            enrichment_suggestions_count = 0
            enrichment_duration_ms: int | None = None
            enrichment_warning: str | None = None
            raw_products = result.get("products")
            products = (
                [item for item in raw_products if isinstance(item, dict)]
                if isinstance(raw_products, list)
                else []
            )
            if isinstance(raw_products, list):
                result["products"] = products
            if products:
                if not shop_domain:
                    enrichment_warning = "Import enrichment skipped: missing shop_domain"
                    emit_and_persist(
                        phase="import_enrichment_skipped",
                        message=enrichment_warning,
                        level="warning",
                    )
                else:
                    enrichment_attempted = True
                    enrichment_started_at = perf_counter()
                    try:
                        normalization_settings = None
                        intelligence = getattr(supabase, "intelligence", None)
                        if intelligence and hasattr(
                            intelligence, "get_product_intelligence_normalization_settings"
                        ):
                            normalization_settings = intelligence.get_product_intelligence_normalization_settings(
                                shop_domain=shop_domain
                            )
                        suggestions = await generate_suggestions_execute(
                            supabase=supabase,
                            products=products,
                            shop_domain=shop_domain,
                            normalization_settings=normalization_settings,
                            trace_event=trace_event,
                        )
                        enrichment_suggestions_count = len(suggestions)
                        enriched_products, variant_operations_by_index = (
                            apply_suggestions_to_products(
                                products=products,
                                suggestions=suggestions,
                            )
                        )
                        for (
                            product_index,
                            variant_operations,
                        ) in variant_operations_by_index.items():
                            if (
                                0 <= product_index < len(enriched_products)
                                and variant_operations
                            ):
                                enriched_products[product_index][
                                    VARIANT_OPERATIONS_FIELD
                                ] = variant_operations
                        result["products"] = enriched_products
                        enrichment_applied = bool(enrichment_suggestions_count)
                    except Exception as exc:
                        enrichment_warning = f"Import enrichment skipped: {exc}"
                        emit_and_persist(
                            phase="import_enrichment_skipped",
                            message=enrichment_warning,
                            level="warning",
                            payload_preview={"shop_domain": shop_domain},
                        )
                    finally:
                        enrichment_duration_ms = int(
                            (perf_counter() - enrichment_started_at) * 1000
                        )
            result["enrichment_attempted"] = enrichment_attempted
            result["enrichment_applied"] = enrichment_applied
            result["enrichment_suggestions_count"] = enrichment_suggestions_count
            if enrichment_duration_ms is not None:
                result["enrichment_duration_ms"] = enrichment_duration_ms
            if enrichment_warning:
                result["enrichment_warning"] = enrichment_warning
        emit_and_persist(phase="workflow_done", message="Document workflow completed")
    except Exception as exc:
        emit_and_persist(
            phase="workflow_error",
            message="Document workflow failed",
            level="error",
            error=str(exc),
        )
        duration_ms = int(
            (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
        )
        try:
            supabase.runs.finalize_run(
                run_id, status="error", duration_ms=duration_ms, error=str(exc)
            )
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
                        "text/csv"
                        if generated_filename.lower().endswith(".csv")
                        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    supabase.file.save_file(
                        generated_file_id,
                        name=generated_filename,
                        content=out_bytes,
                        content_type=ct,
                        file_origin="workflow_output",
                    )
                    try:
                        os.remove(result)
                    except Exception:
                        pass
                    result = {
                        "workbook_path": result,
                        "file_id": generated_file_id,
                        "filename": generated_filename,
                    }
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
            "enrichment_attempted": result.get("enrichment_attempted"),
            "enrichment_applied": result.get("enrichment_applied"),
            "enrichment_suggestions_count": result.get("enrichment_suggestions_count"),
            "enrichment_warning": result.get("enrichment_warning"),
        }
    duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    try:
        supabase.runs.finalize_run(
            run_id, status="success", duration_ms=duration_ms, extra_fields=output_meta
        )
    except Exception:
        pass
    try:
        tracing.complete_run(run_id)
    except Exception:
        pass

    return {"run_id": run_id, "result": result}
