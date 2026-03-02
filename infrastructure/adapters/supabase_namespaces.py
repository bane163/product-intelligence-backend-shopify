from typing import Any


class SupabaseNamespace:
    def __init__(
        self,
        target: Any,
        methods: tuple[str, ...],
        *,
        aliases: dict[str, str] | None = None,
    ) -> None:
        self._target = target
        self._methods = set(methods)
        self._aliases = aliases or {}

    def __getattr__(self, name: str) -> Any:
        resolved = self._aliases.get(name, name)
        if resolved not in self._methods:
            raise AttributeError(name)
        return getattr(self._target, resolved)


class SupabaseDomainAccessors:
    def __init__(self, target: Any) -> None:
        self.file = SupabaseNamespace(
            target,
            (
                "save_file",
                "save_files",
                "list_files",
                "get_file",
                "delete_file",
                "save_file_thumbnail",
                "get_file_thumbnail",
            ),
            aliases={
                "save": "save_file",
                "save_many": "save_files",
                "list": "list_files",
                "get": "get_file",
                "delete": "delete_file",
                "save_thumbnail": "save_file_thumbnail",
                "get_thumbnail": "get_file_thumbnail",
            },
        )
        self.runs = SupabaseNamespace(
            target,
            (
                "create_or_update_run",
                "append_run_event",
                "append_run_message",
                "finalize_run",
                "list_runs",
                "get_run",
                "get_run_history",
                "enqueue_offload_job",
                "claim_next_offload_job",
                "update_offload_job",
                "get_offload_job",
            ),
            aliases={
                "create_or_update": "create_or_update_run",
                "append_event": "append_run_event",
                "append_message": "append_run_message",
                "finalize": "finalize_run",
                "list": "list_runs",
                "get": "get_run",
                "history": "get_run_history",
                "enqueue": "enqueue_offload_job",
                "claim_next": "claim_next_offload_job",
                "update_job": "update_offload_job",
                "get_job": "get_offload_job",
            },
        )
        self.drafts = SupabaseNamespace(
            target,
            (
                "save_product_draft",
                "list_product_drafts",
                "get_product_draft",
                "delete_product_draft",
            ),
            aliases={
                "save": "save_product_draft",
                "list": "list_product_drafts",
                "get": "get_product_draft",
                "delete": "delete_product_draft",
            },
        )
        self.submitted = SupabaseNamespace(
            target,
            (
                "save_submitted_document",
                "list_submitted_documents",
                "get_submitted_document",
                "delete_submitted_document",
            ),
            aliases={
                "save": "save_submitted_document",
                "list": "list_submitted_documents",
                "get": "get_submitted_document",
                "delete": "delete_submitted_document",
            },
        )
        self.intelligence = SupabaseNamespace(
            target,
            (
                "save_product_intelligence_audit",
                "save_product_intelligence_findings",
                "list_product_intelligence_audits",
                "get_product_intelligence_audit",
                "save_product_intelligence_suggestions",
                "list_product_intelligence_suggestions",
                "get_product_intelligence_suggestion",
                "create_product_intelligence_suggestion",
                "mark_product_intelligence_suggestion_applied",
                "mark_product_intelligence_suggestion_pending",
                "get_product_intelligence_normalization_settings",
                "upsert_product_intelligence_normalization_settings",
            ),
        )
        self.llm_configs = SupabaseNamespace(
            target,
            (
                "list_llm_model_configs",
                "create_llm_model_config",
                "update_llm_model_config",
                "delete_llm_model_config",
                "activate_llm_model_config",
                "get_active_llm_model_config",
            ),
            aliases={
                "list": "list_llm_model_configs",
                "create": "create_llm_model_config",
                "update": "update_llm_model_config",
                "delete": "delete_llm_model_config",
                "activate": "activate_llm_model_config",
                "get_active": "get_active_llm_model_config",
            },
        )
