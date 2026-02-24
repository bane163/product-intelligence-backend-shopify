"""Use-case: create LLM model config via supabase port."""
from typing import Any
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(supabase: SupabaseNamespacedPort, payload: dict[str, Any]) -> dict[str, Any]:
    return supabase.llm_configs.create_llm_model_config(**payload)
