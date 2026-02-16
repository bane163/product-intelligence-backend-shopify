"""Use-case: delete an LLM model config via supabase port."""
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, config_id: str, shop_domain: str) -> bool:
    return supabase.delete_llm_model_config(config_id, shop_domain=shop_domain)
