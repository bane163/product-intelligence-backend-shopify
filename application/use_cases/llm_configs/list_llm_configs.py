from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, shop_domain: str):
    return supabase.list_llm_model_configs(shop_domain)
