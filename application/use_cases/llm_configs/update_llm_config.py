"""Use-case: update LLM model config via supabase port."""
from typing import Any
from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, config_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    return supabase.update_llm_model_config(config_id, **payload)
