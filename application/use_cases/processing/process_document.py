from datetime import datetime, timezone
import os
from time import perf_counter
from typing import Any, Callable
import uuid

from ai.models import ProductsList
from app_context import AppContext

from application.domain.product_intelligence_patching import (
    VARIANT_OPERATIONS_FIELD,
    apply_suggestions_to_products,
)
from application.domain.extraction_quality import build_quality_report
from application.domain.field_derivation_policy import apply_server_owned_origins
from application.ports.llm_port import ExtractionRequest, LLMPort, ModelConfiguration
from application.ports.supabase_port import SupabaseNamespacedPort
from application.ports.tracing_port import TracingPort
from application.services.document_formats import classify_document, validate_document_content
from application.services.canonical_pdf import (
    DocumentTextEvidenceUnavailable,
    ensure_canonical_pdf,
)
from application.services.spreadsheet_source_refs import enrich_spreadsheet_source_refs
from application.services.pdf_evidence_index import (
    EvidenceCatalog,
    SourceEvidenceValidationFailed,
    blank_unverified_optional_pdf_values,
    build_evidence_catalog,
    resolve_indexed_pdf_source_refs,
    validate_indexed_pdf_evidence,
)
from application.use_cases.intelligence_generate_suggestions import (
    execute as generate_suggestions_execute,
)
from shared.observability import current_observability_fields

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
    shop_domain: str | None = None,
    manage_lifecycle: bool = True,
    source_file_id: str | None = None,
    enable_ai_enhancements: bool = True,
):
    """Extracted application use-case for processing an uploaded document.

    This implementation mirrors the previous behavior from api/agents/files.process_excel
    but is isolated in the application layer. It currently uses AppContext directly for simplicity.
    """
    run_id = run_id or str(uuid.uuid4())
    observability_fields = current_observability_fields()
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

    if manage_lifecycle:
        supabase.runs.create_or_update_run(run_id, {
            "status": "queued",
            "source": "document_import",
            "started_at": started_at.isoformat(),
            "prompt": DEFAULT_IMPORT_AGENT_PROMPT,
            "writer_prompt": DEFAULT_IMPORT_WRITER_PROMPT,
            "attempt": 1,
            "shop_domain": shop_domain,
            **observability_fields,
        })
    try:
        emit_and_persist(
            phase="request_received",
            message="Received /agents/import request",
            payload_preview={
                "has_file_id": False,
                "extraction_mode": extraction_mode,
            },
        )

        # Persist input metadata
        if manage_lifecycle:
            supabase.runs.create_or_update_run(
            run_id,
            {
                "input_file_id": source_file_id,
                "input_filename": input_name,
                "input_content_type": input_content_type,
                "input_size_bytes": len(file_bytes),
            },
        )

        document_format = classify_document(
            filename=input_name,
            content_type=input_content_type,
            file_bytes=file_bytes,
        )
        try:
            validate_document_content(document_format, file_bytes=file_bytes)
        except ValueError as exc:
            raise ValueError(f"Invalid file content: {exc}") from exc

        model_config = ModelConfiguration(
            provider="openai",
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model_id=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
            api_key=os.getenv("OPENAI_API_KEY", ""),
        )
        if shop_domain:
            active_model = supabase.llm_configs.get_active_llm_model_config(shop_domain)
            if active_model:
                model_config = ModelConfiguration(
                    provider=str(active_model.get("provider") or "openai"),
                    base_url=str(active_model.get("base_url") or ""),
                    model_id=str(active_model.get("model_id") or ""),
                    api_key=str(active_model.get("api_key") or ""),
                    max_tokens=(int(active_model["max_tokens"]) if active_model.get("max_tokens") is not None else None),
                    timeout_seconds=(float(active_model["timeout_seconds"]) if active_model.get("timeout_seconds") is not None else None),
                    extra=(active_model.get("extra") if isinstance(active_model.get("extra"), dict) else None),
                )
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
                        "copilot_byok": True,
                    },
                )

        emit_and_persist(
            phase="workflow_start",
            message="Starting document workflow execution",
            payload_preview={"input_bytes": len(file_bytes)},
        )
        if manage_lifecycle:
            supabase.runs.create_or_update_run(
            run_id,
            {
                "status": "running",
                "shop_domain": shop_domain,
            },
        )

        pdf_layout = None
        evidence_catalog: EvidenceCatalog | None = None
        canonical_source_file_id = source_file_id
        if document_format.kind in {"pdf", "docx", "pptx", "image"}:
            if not source_file_id or not shop_domain:
                raise RuntimeError("Canonical sources require a persisted tenant-owned input file")
            emit_and_persist(
                phase="canonicalization_start",
                message="Resolving canonical PDF source",
                payload_preview={"source_kind": document_format.kind},
            )
            try:
                canonical = await ensure_canonical_pdf(
                    supabase=supabase,
                    collabora=ctx.services.collabora,
                    source_file_id=source_file_id,
                    source_content=file_bytes,
                    source_name=input_name or source_file_id,
                    source_content_type=input_content_type or "application/octet-stream",
                    document_format=document_format,
                    shop_domain=shop_domain,
                    collabora_base_url=collabora_url,
                )
            except Exception as exc:
                emit_and_persist(
                    phase="canonicalization_failure",
                    message="Canonical PDF creation failed",
                    level="error",
                    error=str(exc),
                )
                raise
            canonical_source_file_id = canonical.file_id
            file_bytes = canonical.content
            input_name = canonical.filename
            input_content_type = canonical.content_type
            emit_and_persist(
                phase="canonicalization_cache_hit" if canonical.cache_hit else "canonicalization_success",
                message="Reused canonical PDF" if canonical.cache_hit else "Created canonical PDF",
                payload_preview={"canonical_file_id": canonical.file_id},
            )
            layout_service = ctx.services.document_layout
            if layout_service is None:
                raise RuntimeError("PDF_LAYOUT_UNAVAILABLE: document layout service is not registered")
            pdf_layout = await layout_service.analyze_pdf(file_bytes, canonical_source_file_id)
            if document_format.kind == "image" and not pdf_layout.anchors:
                emit_and_persist(
                    phase="canonicalization_no_text",
                    message="Standalone image contains no textual evidence",
                    level="error",
                )
                raise DocumentTextEvidenceUnavailable(
                    "Azure processed the image but returned no usable textual anchors"
                )
            evidence_catalog = build_evidence_catalog(pdf_layout)
            emit_and_persist(
                phase="pdf_page_coverage",
                message="Resolved PDF layout coverage",
                payload_preview={"provider": pdf_layout.provider, "expected": list(pdf_layout.expected_pages), "covered": list(pdf_layout.covered_pages), "anchors": len(pdf_layout.anchors)},
            )
            emit_and_persist(
                phase="pdf_evidence_catalog_built",
                message="Built deterministic PDF evidence catalog",
                payload_preview={
                    "catalog_size": len(evidence_catalog.entries),
                    "layout_anchors": len(pdf_layout.anchors),
                    "provider": pdf_layout.provider,
                },
            )

        max_evidence_attempts = 3 if evidence_catalog is not None else 1
        result: ProductsList | dict[str, Any] | str | None = None
        validation_feedback: str | None = None
        for evidence_attempt in range(max_evidence_attempts):
            result = await llm.extract(
                ExtractionRequest(
                    document_kind=document_format.kind,
                    content=file_bytes,
                    filename=input_name or "uploaded-document",
                    content_type=input_content_type or "application/octet-stream",
                    model=model_config,
                    evidence_catalog=evidence_catalog,
                    validation_feedback=validation_feedback,
                    extraction_mode=("first_sheet" if extraction_mode.strip().lower() == "first_sheet" else "per_sheet"),
                    collabora_base_url=collabora_url,
                    trace_event=trace_event,
                )
            )
            if isinstance(result, ProductsList):
                result = {
                    "products": [
                        item
                        for item in result.model_dump(mode="json", exclude_none=True).get("products", [])
                        if isinstance(item, dict)
                    ]
                }
            if evidence_catalog is None:
                break
            raw_products = result.get("products") if isinstance(result, dict) else None
            candidate_products = (
                [item for item in raw_products if isinstance(item, dict)]
                if isinstance(raw_products, list)
                else []
            )
            for product in candidate_products:
                apply_server_owned_origins(product)
            cleared_optional_paths: tuple[str, ...] = ()
            sanitation_diagnostics: list[dict[str, Any]] = []
            if evidence_attempt + 1 >= max_evidence_attempts:
                cleared_optional_paths = blank_unverified_optional_pdf_values(
                    candidate_products,
                    evidence_catalog,
                    diagnostics=sanitation_diagnostics,
                )
            if cleared_optional_paths:
                emit_and_persist(
                    phase="pdf_unverified_optional_values_cleared",
                    message="Left optional PDF fields blank when no indexed evidence exists",
                    payload_preview={
                        "value_paths": list(cleared_optional_paths),
                        "products": sanitation_diagnostics,
                    },
                )
            validation = validate_indexed_pdf_evidence(candidate_products, evidence_catalog)
            if validation.valid:
                # Sanitation mutates the model-dumped dictionaries. Verify that
                # pruning optional leaves did not invalidate the ProductsList
                # contract before source-reference resolution mutates them again.
                try:
                    ProductsList.model_validate({"products": candidate_products})
                except Exception as exc:
                    schema_paths = tuple(
                        ".".join(str(part) for part in error.get("loc", ()))
                        for error in getattr(exc, "errors", lambda: [])()
                    ) or ("products",)
                    feedback = (
                        "Sanitized PDF payload failed ProductsList validation: "
                        + ", ".join(schema_paths)
                    )
                    emit_and_persist(
                        phase="pdf_evidence_validation_failed",
                        message="Sanitized PDF payload is not a complete ProductsList",
                        level="error",
                        payload_preview={
                            "attempts": evidence_attempt + 1,
                            "affected_paths": list(schema_paths),
                            "products": sanitation_diagnostics,
                        },
                        error=feedback,
                    )
                    raise SourceEvidenceValidationFailed(schema_paths, feedback) from exc
                resolution_stats: dict[str, int] = {}
                resolved = resolve_indexed_pdf_source_refs(
                    candidate_products,
                    evidence_catalog,
                    canonical_source_file_id or input_name or "uploaded.pdf",
                    pdf_content=file_bytes,
                    resolution_stats=resolution_stats,
                )
                if isinstance(result, dict):
                    result["products"] = candidate_products
                emit_and_persist(
                    phase="pdf_evidence_validation_success",
                    message="Validated and resolved indexed PDF evidence",
                    payload_preview={
                        "attempt": evidence_attempt + 1,
                        "resolved": resolved,
                        "exact_regions": resolution_stats.get("exact", 0),
                        "coarse_regions": resolution_stats.get("coarse", 0),
                        "catalog_size": len(evidence_catalog.entries),
                    },
                )
                break
            emit_and_persist(
                phase="pdf_evidence_missing_index_feedback",
                message="PDF extraction requires source-evidence correction",
                level="warning",
                payload_preview={
                    "attempt": evidence_attempt + 1,
                    "affected_paths": list(validation.affected_paths),
                    "feedback": validation.feedback,
                },
            )
            if evidence_attempt + 1 >= max_evidence_attempts:
                emit_and_persist(
                    phase="pdf_evidence_validation_failed",
                    message="PDF source-evidence validation failed after corrections",
                    level="error",
                    payload_preview={
                        "attempts": max_evidence_attempts,
                        "affected_paths": list(validation.affected_paths),
                    },
                    error=validation.feedback,
                )
                raise SourceEvidenceValidationFailed(
                    validation.affected_paths, validation.feedback
                )
            emit_and_persist(
                phase="pdf_evidence_correction_attempt",
                message="Requesting a complete corrected product payload",
                level="warning",
                payload_preview={
                    "correction_attempt": evidence_attempt + 1,
                    "affected_paths": list(validation.affected_paths),
                },
            )
            validation_feedback = validation.feedback
        if isinstance(result, dict):
            # Extraction returns product data only. Ignore legacy writer metadata
            # from older adapters so it cannot leak back into the import contract.
            result.pop("file_id", None)
            result.pop("filename", None)
            result.pop("workbook_path", None)
            enrichment_attempted = False
            enrichment_applied = False
            enrichment_suggestions_count = 0
            enrichment_duration_ms: int | None = None
            enrichment_warning: str | None = None
            applied_suggestions: list[dict] = []
            raw_products = result.get("products")
            products = (
                [item for item in raw_products if isinstance(item, dict)]
                if isinstance(raw_products, list)
                else []
            )
            if isinstance(raw_products, list):
                result["products"] = products
            if products and document_format.is_spreadsheet:
                enrich_spreadsheet_source_refs(
                    products, file_bytes, input_name, source_file_id=source_file_id
                )
            for product in products:
                refs = product.get("source_refs") if isinstance(product.get("source_refs"), list) else []
                origins = product.get("value_origins") if isinstance(product.get("value_origins"), dict) else {}
                for ref in refs:
                    if isinstance(ref, dict) and isinstance(ref.get("field"), str) and ref["field"]:
                        origins.setdefault(str(ref.get("value_path") or ref["field"]), "source")
                if origins:
                    product["value_origins"] = origins
            if products and enable_ai_enhancements:
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
                        applied_suggestions = suggestions
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
            final_products = result.get("products")
            result["quality_report"] = build_quality_report(
                [item for item in final_products if isinstance(item, dict)]
                if isinstance(final_products, list)
                else []
            )
            result["quality_gate_status"] = "ready"
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
            if not manage_lifecycle:
                raise RuntimeError("lifecycle managed by offload worker")
            supabase.runs.finalize_run(
                run_id, status="error", duration_ms=duration_ms, error=str(exc)
            )
        except Exception:
            pass
        try:
            if not manage_lifecycle:
                raise RuntimeError("lifecycle managed by offload worker")
            tracing.complete_run(run_id)
        except Exception:
            pass
        raise

    # Cleanup input file id if present
    try:
        # No input file id stored by this use-case; caller handles deletion when appropriate.
        pass
    except Exception:
        pass

    output_meta = {}
    if isinstance(result, dict):
        output_meta = {
            "enrichment_attempted": result.get("enrichment_attempted"),
            "enrichment_applied": result.get("enrichment_applied"),
            "enrichment_suggestions_count": result.get("enrichment_suggestions_count"),
            "enrichment_warning": result.get("enrichment_warning"),
        }
    duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    try:
        if not manage_lifecycle:
            raise RuntimeError("lifecycle managed by offload worker")
        supabase.runs.finalize_run(
            run_id, status="success", duration_ms=duration_ms, extra_fields=output_meta
        )
    except Exception:
        pass
    try:
        if not manage_lifecycle:
            raise RuntimeError("lifecycle managed by offload worker")
        tracing.complete_run(run_id)
    except Exception:
        pass

    return {"run_id": run_id, "result": result}
