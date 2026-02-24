"""Use-case: delete an LLM model config via supabase port."""
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(supabase: SupabaseNamespacedPort, config_id: str, shop_domain: str) -> bool:
    return supabase.llm_configs.delete_llm_model_config(config_id, shop_domain=shop_domain)
