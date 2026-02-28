from typing import Any

from application.ports.supabase_port import (
    SupabaseDraftsNamespacePort,
    SupabaseFileNamespacePort,
    SupabaseIntelligenceNamespacePort,
    SupabaseLlmConfigsNamespacePort,
    SupabaseNamespacedPort,
    SupabaseRunsNamespacePort,
    SupabaseSubmittedNamespacePort,
)
from services.interfaces import SupabaseServiceInterface
from .supabase_namespaces import SupabaseDomainAccessors


class SupabaseAdapter(SupabaseNamespacedPort):
    """Adapter that proxies calls to the real SupabaseService instance.

    This keeps the application layer depending on an interface/port while
    reusing the existing SupabaseService implementation.
    """

    file: SupabaseFileNamespacePort
    runs: SupabaseRunsNamespacePort
    drafts: SupabaseDraftsNamespacePort
    submitted: SupabaseSubmittedNamespacePort
    intelligence: SupabaseIntelligenceNamespacePort
    llm_configs: SupabaseLlmConfigsNamespacePort

    def __init__(self, service: SupabaseServiceInterface) -> None:
        self._service = service
        domains = SupabaseDomainAccessors(self)
        self.file = domains.file
        self.runs = domains.runs
        self.drafts = domains.drafts
        self.submitted = domains.submitted
        self.intelligence = domains.intelligence
        self.llm_configs = domains.llm_configs

    def save_file(
        self,
        file_id: str,
        name: str,
        content: bytes,
        content_type: str | None = None,
        file_origin: str | None = None,
    ) -> None:
        return self._service.save_file(
            file_id,
            name,
            content,
            content_type,
            file_origin=file_origin,
        )

    def list_files(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self._service.list_files(limit, offset)

    def get_file(self, file_id: str) -> dict[str, Any] | None:
        return self._service.get_file(file_id)

    def delete_file(self, file_id: str) -> bool:
        return self._service.delete_file(file_id)

    def save_file_thumbnail(self, *, file_id: str, content: bytes) -> str | None:
        return self._service.save_file_thumbnail(file_id=file_id, content=content)

    def get_file_thumbnail(self, file_id: str) -> bytes | None:
        return self._service.get_file_thumbnail(file_id)

    def create_or_update_run(self, run_id: str, fields: dict[str, Any]) -> None:
        return self._service.create_or_update_run(run_id, fields)

    def append_run_event(self, run_id: str, event: dict[str, Any], seq: int) -> None:
        return self._service.append_run_event(run_id, event, seq)

    def append_run_message(
        self,
        run_id: str,
        *,
        role: str,
        message: Any,
        seq: int,
        meta: dict[str, Any] | None = None,
    ) -> None:
        return self._service.append_run_message(
            run_id, role=role, message=message, seq=seq, meta=meta
        )

    def finalize_run(
        self,
        run_id: str,
        *,
        status: str,
        duration_ms: int | None = None,
        error: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        return self._service.finalize_run(
            run_id,
            status=status,
            duration_ms=duration_ms,
            error=error,
            extra_fields=extra_fields,
        )

    def list_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._service.list_runs(limit, offset, status, shop_domain)

    def get_run(self, run_id: str, *, shop_domain: str | None = None) -> dict[str, Any] | None:
        return self._service.get_run(run_id, shop_domain=shop_domain)

    def get_run_history(self, run_id: str, *, shop_domain: str | None = None) -> dict[str, Any]:
        return self._service.get_run_history(run_id, shop_domain=shop_domain)

    def enqueue_offload_job(
        self,
        job_id: str,
        fields: dict[str, Any],
        *,
        require_persistent_queue: bool = False,
    ) -> dict[str, Any] | None:
        return self._service.enqueue_offload_job(
            job_id, fields, require_persistent_queue=require_persistent_queue
        )

    def claim_next_offload_job(
        self,
        *,
        queue_name: str = "default",
        worker_id: str,
        lease_seconds: int = 300,
    ) -> dict[str, Any] | None:
        return self._service.claim_next_offload_job(
            queue_name=queue_name,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )

    def update_offload_job(
        self, job_id: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        return self._service.update_offload_job(job_id, fields)

    def get_offload_job(self, job_id: str) -> dict[str, Any] | None:
        return self._service.get_offload_job(job_id)

    def save_product_draft(
        self,
        *,
        draft_id: str,
        run_id: str | None,
        import_mode: str,
        draft_name: str | None,
        shop_domain: str | None = None,
        input_file_id: str | None = None,
        input_filename: str | None = None,
        output_file_id: str | None = None,
        output_filename: str | None = None,
        extraction_status: str | None = None,
        extraction_run_id: str | None = None,
        extraction_error: str | None = None,
        submit_status: str | None = None,
        submit_run_id: str | None = None,
        submit_error: str | None = None,
        require_lifecycle_columns: bool = False,
        products: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._service.save_product_draft(
            draft_id=draft_id,
            run_id=run_id,
            import_mode=import_mode,
            draft_name=draft_name,
            shop_domain=shop_domain,
            input_file_id=input_file_id,
            input_filename=input_filename,
            output_file_id=output_file_id,
            output_filename=output_filename,
            extraction_status=extraction_status,
            extraction_run_id=extraction_run_id,
            extraction_error=extraction_error,
            submit_status=submit_status,
            submit_run_id=submit_run_id,
            submit_error=submit_error,
            require_lifecycle_columns=require_lifecycle_columns,
            products=products,
        )

    def list_product_drafts(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        sort_by: str = "date",
        sort_dir: str = "desc",
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._service.list_product_drafts(
            limit,
            offset,
            search,
            sort_by,
            sort_dir,
            shop_domain,
        )

    def get_product_draft(
        self, draft_id: str, *, shop_domain: str | None = None
    ) -> dict[str, Any] | None:
        return self._service.get_product_draft(draft_id, shop_domain=shop_domain)

    def delete_product_draft(
        self, draft_id: str, *, shop_domain: str | None = None
    ) -> bool:
        return self._service.delete_product_draft(draft_id, shop_domain=shop_domain)

    def save_submitted_document(
        self,
        *,
        submitted_id: str,
        run_id: str | None,
        draft_id: str | None,
        name: str,
        import_mode: str,
        shop_domain: str | None = None,
        product_count: int,
        products: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._service.save_submitted_document(
            submitted_id=submitted_id,
            run_id=run_id,
            draft_id=draft_id,
            name=name,
            import_mode=import_mode,
            shop_domain=shop_domain,
            product_count=product_count,
            products=products,
        )

    def list_submitted_documents(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        sort_by: str = "date",
        sort_dir: str = "desc",
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._service.list_submitted_documents(
            limit,
            offset,
            search,
            sort_by,
            sort_dir,
            shop_domain,
        )

    def get_submitted_document(
        self, submitted_id: str, *, shop_domain: str | None = None
    ) -> dict[str, Any] | None:
        return self._service.get_submitted_document(
            submitted_id,
            shop_domain=shop_domain,
        )

    def delete_submitted_document(
        self, submitted_id: str, *, shop_domain: str | None = None
    ) -> bool:
        return self._service.delete_submitted_document(
            submitted_id,
            shop_domain=shop_domain,
        )

    def save_product_intelligence_audit(
        self,
        *,
        audit_id: str,
        run_id: str | None,
        submitted_id: str | None,
        scope: str,
        status: str,
        overall_score: int,
        findings_count: int,
        component_scores: dict[str, int],
        totals: dict[str, Any],
        shop_domain: str | None = None,
    ) -> dict[str, Any]:
        return self._service.save_product_intelligence_audit(
            audit_id=audit_id,
            run_id=run_id,
            submitted_id=submitted_id,
            scope=scope,
            status=status,
            overall_score=overall_score,
            findings_count=findings_count,
            component_scores=component_scores,
            totals=totals,
            shop_domain=shop_domain,
        )

    def save_product_intelligence_findings(
        self,
        *,
        audit_id: str,
        findings: list[dict[str, Any]],
        shop_domain: str | None = None,
    ) -> int:
        return self._service.save_product_intelligence_findings(
            audit_id=audit_id,
            findings=findings,
            shop_domain=shop_domain,
        )

    def list_product_intelligence_audits(
        self,
        *,
        shop_domain: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self._service.list_product_intelligence_audits(
            shop_domain=shop_domain,
            limit=limit,
            offset=offset,
        )

    def get_product_intelligence_audit(
        self,
        audit_id: str,
        *,
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None:
        return self._service.get_product_intelligence_audit(
            audit_id,
            shop_domain=shop_domain,
        )

    def save_product_intelligence_suggestions(
        self,
        *,
        audit_id: str,
        suggestions: list[dict[str, Any]],
        shop_domain: str | None = None,
    ) -> int:
        return self._service.save_product_intelligence_suggestions(
            audit_id=audit_id,
            suggestions=suggestions,
            shop_domain=shop_domain,
        )

    def list_product_intelligence_suggestions(
        self,
        *,
        audit_id: str,
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._service.list_product_intelligence_suggestions(
            audit_id=audit_id,
            shop_domain=shop_domain,
        )

    def get_product_intelligence_suggestion(
        self,
        suggestion_id: str,
        *,
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None:
        return self._service.get_product_intelligence_suggestion(
            suggestion_id,
            shop_domain=shop_domain,
        )

    def create_product_intelligence_suggestion(
        self,
        *,
        suggestion: dict[str, Any],
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None:
        return self._service.create_product_intelligence_suggestion(
            suggestion=suggestion,
            shop_domain=shop_domain,
        )

    def mark_product_intelligence_suggestion_applied(
        self,
        *,
        suggestion_id: str,
        previous_payload: dict[str, Any] | None = None,
        patch_payload: dict[str, Any] | None = None,
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None:
        return self._service.mark_product_intelligence_suggestion_applied(
            suggestion_id=suggestion_id,
            previous_payload=previous_payload,
            patch_payload=patch_payload,
            shop_domain=shop_domain,
        )

    def mark_product_intelligence_suggestion_pending(
        self,
        *,
        suggestion_id: str,
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None:
        return self._service.mark_product_intelligence_suggestion_pending(
            suggestion_id=suggestion_id,
            shop_domain=shop_domain,
        )

    def get_product_intelligence_normalization_settings(
        self,
        *,
        shop_domain: str,
    ) -> dict[str, Any] | None:
        return self._service.get_product_intelligence_normalization_settings(
            shop_domain=shop_domain,
        )

    def upsert_product_intelligence_normalization_settings(
        self,
        *,
        shop_domain: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        return self._service.upsert_product_intelligence_normalization_settings(
            shop_domain=shop_domain,
            settings=settings,
        )

    def list_llm_model_configs(self, shop_domain: str) -> list[dict[str, Any]]:
        return self._service.list_llm_model_configs(shop_domain)

    def create_llm_model_config(
        self,
        *,
        shop_domain: str,
        name: str,
        provider: str,
        base_url: str,
        model_id: str,
        api_key: str,
        version: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: int | None = None,
        is_active: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._service.create_llm_model_config(
            shop_domain=shop_domain,
            name=name,
            provider=provider,
            base_url=base_url,
            model_id=model_id,
            api_key=api_key,
            version=version,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            is_active=is_active,
            extra=extra,
        )

    def update_llm_model_config(
        self,
        config_id: str,
        *,
        name: str | None = None,
        provider: str | None = None,
        base_url: str | None = None,
        model_id: str | None = None,
        api_key: str | None = None,
        version: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self._service.update_llm_model_config(
            config_id,
            name=name,
            provider=provider,
            base_url=base_url,
            model_id=model_id,
            api_key=api_key,
            version=version,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            extra=extra,
        )

    def delete_llm_model_config(self, config_id: str, *, shop_domain: str) -> bool:
        return self._service.delete_llm_model_config(config_id, shop_domain=shop_domain)

    def activate_llm_model_config(
        self, config_id: str, *, shop_domain: str
    ) -> dict[str, Any] | None:
        return self._service.activate_llm_model_config(config_id, shop_domain=shop_domain)

    def get_active_llm_model_config(
        self, shop_domain: str
    ) -> dict[str, Any] | None:
        return self._service.get_active_llm_model_config(shop_domain)
