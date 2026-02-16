"""Use-case: activate an LLM model config via supabase port."""
from typing import Any
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, config_id: str, shop_domain: str) -> dict[str, Any] | None:
    return supabase.activate_llm_model_config(config_id, shop_domain=shop_domain)
