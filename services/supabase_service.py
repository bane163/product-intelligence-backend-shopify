import os
from typing import Any

from .interfaces import SupabaseServiceInterface
from .supabase_billing_mixin import SupabaseBillingMixin
from .supabase_drafts_mixin import SupabaseDraftsMixin
from .supabase_file_mixin import SupabaseFileMixin
from .supabase_intelligence_mixin import SupabaseIntelligenceMixin
from .supabase_llm_config_mixin import SupabaseLlmConfigMixin
from .supabase_runs_mixin import SupabaseRunsMixin


class SupabaseService(
    SupabaseFileMixin,
    SupabaseRunsMixin,
    SupabaseDraftsMixin,
    SupabaseIntelligenceMixin,
    SupabaseLlmConfigMixin,
    SupabaseBillingMixin,
    SupabaseServiceInterface,
):
    def _utc_now(self) -> str:
        return SupabaseRunsMixin._utc_now()

    def __init__(self, bucket_name: str | None = None):
        self.bucket_name = bucket_name or os.environ.get(
            "FILES_BUCKET_NAME", "documents"
        )
        self.file_storage: dict[str, dict[str, Any]] = {}
        self.offload_jobs: dict[str, dict[str, Any]] = {}
        self.product_drafts: dict[str, dict[str, Any]] = {}
        self.submitted_documents: dict[str, dict[str, Any]] = {}
        self.product_intelligence_audits: dict[str, dict[str, Any]] = {}
        self.product_intelligence_findings: dict[str, list[dict[str, Any]]] = {}
        self.product_intelligence_suggestions: dict[str, dict[str, Any]] = {}
        self.product_intelligence_bulk_operations: dict[str, dict[str, Any]] = {}
        self.product_intelligence_normalization_settings: dict[str, dict[str, Any]] = {}
        self.llm_model_configs: dict[str, dict[str, Any]] = {}
        self._billing_store: dict[str, dict[str, Any]] = {}
