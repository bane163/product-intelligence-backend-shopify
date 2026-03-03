from typing import Any, Protocol


class SupabasePort(Protocol):
    def save_file(
        self,
        file_id: str,
        name: str,
        content: bytes,
        content_type: str | None = None,
        file_origin: str | None = None,
    ) -> None: ...

    def save_files(self, files: list[dict[str, Any]]) -> None: ...

    def list_files(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]: ...

    def get_file(self, file_id: str) -> dict[str, Any] | None: ...

    def delete_file(self, file_id: str) -> bool: ...

    def save_file_thumbnail(self, *, file_id: str, content: bytes) -> str | None: ...

    def get_file_thumbnail(self, file_id: str) -> bytes | None: ...

    def create_or_update_run(self, run_id: str, fields: dict[str, Any]) -> None: ...

    def append_run_event(self, run_id: str, event: dict[str, Any], seq: int) -> None: ...

    def append_run_message(
        self,
        run_id: str,
        *,
        role: str,
        message: Any,
        seq: int,
        meta: dict[str, Any] | None = None,
    ) -> None: ...

    def finalize_run(
        self,
        run_id: str,
        *,
        status: str,
        duration_ms: int | None = None,
        error: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None: ...

    def list_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_run(self, run_id: str, *, shop_domain: str | None = None) -> dict[str, Any] | None: ...

    def get_run_history(self, run_id: str, *, shop_domain: str | None = None) -> dict[str, Any]: ...

    def enqueue_offload_job(
        self,
        job_id: str,
        fields: dict[str, Any],
        *,
        require_persistent_queue: bool = False,
    ) -> dict[str, Any] | None: ...

    def claim_next_offload_job(
        self,
        *,
        queue_name: str = "default",
        worker_id: str,
        lease_seconds: int = 300,
    ) -> dict[str, Any] | None: ...

    def update_offload_job(
        self, job_id: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None: ...

    def get_offload_job(self, job_id: str) -> dict[str, Any] | None: ...

    def list_offload_jobs_for_run(
        self,
        run_id: str,
        *,
        shop_domain: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...

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
    ) -> dict[str, Any]: ...

    def list_product_drafts(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        sort_by: str = "date",
        sort_dir: str = "desc",
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_product_draft(
        self, draft_id: str, *, shop_domain: str | None = None
    ) -> dict[str, Any] | None: ...

    def delete_product_draft(
        self, draft_id: str, *, shop_domain: str | None = None
    ) -> bool: ...

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
    ) -> dict[str, Any]: ...

    def list_submitted_documents(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        sort_by: str = "date",
        sort_dir: str = "desc",
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_submitted_document(
        self, submitted_id: str, *, shop_domain: str | None = None
    ) -> dict[str, Any] | None: ...

    def delete_submitted_document(
        self, submitted_id: str, *, shop_domain: str | None = None
    ) -> bool: ...

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
    ) -> dict[str, Any]: ...

    def save_product_intelligence_findings(
        self,
        *,
        audit_id: str,
        findings: list[dict[str, Any]],
        shop_domain: str | None = None,
    ) -> int: ...

    def list_product_intelligence_audits(
        self,
        *,
        shop_domain: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...

    def get_product_intelligence_audit(
        self,
        audit_id: str,
        *,
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None: ...

    def save_product_intelligence_suggestions(
        self,
        *,
        audit_id: str,
        suggestions: list[dict[str, Any]],
        shop_domain: str | None = None,
    ) -> int: ...

    def list_product_intelligence_suggestions(
        self,
        *,
        audit_id: str,
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_product_intelligence_suggestion(
        self,
        suggestion_id: str,
        *,
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None: ...

    def create_product_intelligence_suggestion(
        self,
        *,
        suggestion: dict[str, Any],
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None: ...

    def mark_product_intelligence_suggestion_applied(
        self,
        *,
        suggestion_id: str,
        previous_payload: dict[str, Any] | None = None,
        patch_payload: dict[str, Any] | None = None,
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None: ...

    def mark_product_intelligence_suggestion_pending(
        self, *, suggestion_id: str, shop_domain: str | None = None
    ) -> dict[str, Any] | None: ...

    def get_product_intelligence_normalization_settings(
        self,
        *,
        shop_domain: str,
    ) -> dict[str, Any] | None: ...

    def upsert_product_intelligence_normalization_settings(
        self,
        *,
        shop_domain: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]: ...

    def get_product_intelligence_bulk_operation(
        self,
        *,
        operation_type: str,
        idempotency_key: str,
        shop_domain: str,
    ) -> dict[str, Any] | None: ...

    def upsert_product_intelligence_bulk_operation(
        self,
        *,
        operation_type: str,
        idempotency_key: str,
        request_hash: str,
        response: dict[str, Any],
        shop_domain: str,
        status: str = "succeeded",
    ) -> dict[str, Any]: ...

    def list_llm_model_configs(self, shop_domain: str) -> list[dict[str, Any]]: ...

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
    ) -> dict[str, Any]: ...

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
    ) -> dict[str, Any] | None: ...

    def delete_llm_model_config(self, config_id: str, *, shop_domain: str) -> bool: ...

    def activate_llm_model_config(
        self, config_id: str, *, shop_domain: str
    ) -> dict[str, Any] | None: ...

    def get_active_llm_model_config(
        self, shop_domain: str
    ) -> dict[str, Any] | None: ...


class SupabaseFileNamespacePort(Protocol):
    def save_file(
        self,
        file_id: str,
        name: str,
        content: bytes,
        content_type: str | None = None,
        file_origin: str | None = None,
    ) -> None: ...

    def save_files(self, files: list[dict[str, Any]]) -> None: ...

    def list_files(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]: ...

    def get_file(self, file_id: str) -> dict[str, Any] | None: ...

    def delete_file(self, file_id: str) -> bool: ...

    def save_file_thumbnail(self, *, file_id: str, content: bytes) -> str | None: ...

    def get_file_thumbnail(self, file_id: str) -> bytes | None: ...


class SupabaseRunsNamespacePort(Protocol):
    def create_or_update_run(self, run_id: str, fields: dict[str, Any]) -> None: ...

    def append_run_event(self, run_id: str, event: dict[str, Any], seq: int) -> None: ...

    def append_run_message(
        self,
        run_id: str,
        *,
        role: str,
        message: Any,
        seq: int,
        meta: dict[str, Any] | None = None,
    ) -> None: ...

    def finalize_run(
        self,
        run_id: str,
        *,
        status: str,
        duration_ms: int | None = None,
        error: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None: ...

    def list_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_run(self, run_id: str, *, shop_domain: str | None = None) -> dict[str, Any] | None: ...

    def get_run_history(self, run_id: str, *, shop_domain: str | None = None) -> dict[str, Any]: ...

    def enqueue_offload_job(
        self,
        job_id: str,
        fields: dict[str, Any],
        *,
        require_persistent_queue: bool = False,
    ) -> dict[str, Any] | None: ...

    def claim_next_offload_job(
        self,
        *,
        queue_name: str = "default",
        worker_id: str,
        lease_seconds: int = 300,
    ) -> dict[str, Any] | None: ...

    def update_offload_job(
        self, job_id: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None: ...

    def get_offload_job(self, job_id: str) -> dict[str, Any] | None: ...

    def list_offload_jobs_for_run(
        self,
        run_id: str,
        *,
        shop_domain: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...


class SupabaseDraftsNamespacePort(Protocol):
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
    ) -> dict[str, Any]: ...

    def list_product_drafts(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        sort_by: str = "date",
        sort_dir: str = "desc",
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_product_draft(
        self, draft_id: str, *, shop_domain: str | None = None
    ) -> dict[str, Any] | None: ...

    def delete_product_draft(
        self, draft_id: str, *, shop_domain: str | None = None
    ) -> bool: ...


class SupabaseSubmittedNamespacePort(Protocol):
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
    ) -> dict[str, Any]: ...

    def list_submitted_documents(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        sort_by: str = "date",
        sort_dir: str = "desc",
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_submitted_document(
        self, submitted_id: str, *, shop_domain: str | None = None
    ) -> dict[str, Any] | None: ...

    def delete_submitted_document(
        self, submitted_id: str, *, shop_domain: str | None = None
    ) -> bool: ...


class SupabaseIntelligenceNamespacePort(Protocol):
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
    ) -> dict[str, Any]: ...

    def save_product_intelligence_findings(
        self,
        *,
        audit_id: str,
        findings: list[dict[str, Any]],
        shop_domain: str | None = None,
    ) -> int: ...

    def list_product_intelligence_audits(
        self,
        *,
        shop_domain: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...

    def get_product_intelligence_audit(
        self,
        audit_id: str,
        *,
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None: ...

    def save_product_intelligence_suggestions(
        self,
        *,
        audit_id: str,
        suggestions: list[dict[str, Any]],
        shop_domain: str | None = None,
    ) -> int: ...

    def list_product_intelligence_suggestions(
        self,
        *,
        audit_id: str,
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_product_intelligence_suggestion(
        self,
        suggestion_id: str,
        *,
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None: ...

    def create_product_intelligence_suggestion(
        self,
        *,
        suggestion: dict[str, Any],
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None: ...

    def mark_product_intelligence_suggestion_applied(
        self,
        *,
        suggestion_id: str,
        previous_payload: dict[str, Any] | None = None,
        patch_payload: dict[str, Any] | None = None,
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None: ...

    def mark_product_intelligence_suggestion_pending(
        self, *, suggestion_id: str, shop_domain: str | None = None
    ) -> dict[str, Any] | None: ...

    def get_product_intelligence_normalization_settings(
        self,
        *,
        shop_domain: str,
    ) -> dict[str, Any] | None: ...

    def upsert_product_intelligence_normalization_settings(
        self,
        *,
        shop_domain: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]: ...

    def get_product_intelligence_bulk_operation(
        self,
        *,
        operation_type: str,
        idempotency_key: str,
        shop_domain: str,
    ) -> dict[str, Any] | None: ...

    def upsert_product_intelligence_bulk_operation(
        self,
        *,
        operation_type: str,
        idempotency_key: str,
        request_hash: str,
        response: dict[str, Any],
        shop_domain: str,
        status: str = "succeeded",
    ) -> dict[str, Any]: ...


class SupabaseLlmConfigsNamespacePort(Protocol):
    def list_llm_model_configs(self, shop_domain: str) -> list[dict[str, Any]]: ...

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
    ) -> dict[str, Any]: ...

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
    ) -> dict[str, Any] | None: ...

    def delete_llm_model_config(self, config_id: str, *, shop_domain: str) -> bool: ...

    def activate_llm_model_config(
        self, config_id: str, *, shop_domain: str
    ) -> dict[str, Any] | None: ...

    def get_active_llm_model_config(
        self, shop_domain: str
    ) -> dict[str, Any] | None: ...


class SupabaseNamespacedPort(Protocol):
    file: SupabaseFileNamespacePort
    runs: SupabaseRunsNamespacePort
    drafts: SupabaseDraftsNamespacePort
    submitted: SupabaseSubmittedNamespacePort
    intelligence: SupabaseIntelligenceNamespacePort
    llm_configs: SupabaseLlmConfigsNamespacePort
