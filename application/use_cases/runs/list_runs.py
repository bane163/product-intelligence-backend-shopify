"""Use-case: list runs via supabase port."""
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(
    supabase: SupabaseNamespacedPort,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    shop_domain: str | None = None,
) -> list[dict]:
    return supabase.runs.list_runs(
        limit=limit,
        offset=offset,
        status=status,
        shop_domain=shop_domain,
    )
