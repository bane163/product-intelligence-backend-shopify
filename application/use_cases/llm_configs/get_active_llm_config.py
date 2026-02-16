"""Use-case: get active LLM model config via supabase port."""
from typing import Any
from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, shop_domain: str) -> dict[str, Any] | None:
    return supabase.get_active_llm_model_config(shop_domain)
