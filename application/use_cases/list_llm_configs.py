from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, shop_domain: str):
    return supabase.list_llm_model_configs(shop_domain)
