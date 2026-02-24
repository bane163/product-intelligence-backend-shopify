"""Use-case: get active LLM model config via supabase port."""
from typing import Any
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(supabase: SupabaseNamespacedPort, shop_domain: str) -> dict[str, Any] | None:
    return supabase.llm_configs.get_active_llm_model_config(shop_domain)
