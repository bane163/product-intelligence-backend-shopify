"""Use-case: update LLM model config via supabase port."""
from typing import Any
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, config_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    return supabase.update_llm_model_config(config_id, **payload)
