"""Use-case: create LLM model config via supabase port."""
from typing import Any
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, payload: dict[str, Any]) -> dict[str, Any]:
    return supabase.create_llm_model_config(**payload)
