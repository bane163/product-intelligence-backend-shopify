from application.ports.supabase_port import SupabaseNamespacedPort


def execute(supabase: SupabaseNamespacedPort, shop_domain: str):
    return supabase.llm_configs.list_llm_model_configs(shop_domain)
