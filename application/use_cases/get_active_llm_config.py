"""Use-case: get active LLM model config via supabase port."""
from typing import Any
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, shop_domain: str) -> dict[str, Any] | None:
    return supabase.get_active_llm_model_config(shop_domain)
