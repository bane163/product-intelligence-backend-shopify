"""Use-case: update LLM model config via supabase port."""
from typing import Any
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(supabase: SupabaseNamespacedPort, config_id: str, payload: dict[str, Any], shop_domain: str) -> dict[str, Any] | None:
    return supabase.llm_configs.update_llm_model_config(config_id, shop_domain=shop_domain, **payload)
