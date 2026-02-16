"""Use-case: activate an LLM model config via supabase port."""
from typing import Any
from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, config_id: str, shop_domain: str) -> dict[str, Any] | None:
    return supabase.activate_llm_model_config(config_id, shop_domain=shop_domain)
