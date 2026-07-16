from application.ports.supabase_port import SupabaseNamespacedPort


def execute(
    supabase: SupabaseNamespacedPort,
    limit: int = 100,
    offset: int = 0,
    shop_domain: str | None = None,
):
    return supabase.file.list_files(
        limit=limit,
        offset=offset,
        shop_domain=shop_domain,
    )
