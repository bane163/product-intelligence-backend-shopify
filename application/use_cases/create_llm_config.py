"""Use-case: create LLM model config via supabase port."""
from typing import Any
from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, payload: dict[str, Any]) -> dict[str, Any]:
    return supabase.create_llm_model_config(**payload)
