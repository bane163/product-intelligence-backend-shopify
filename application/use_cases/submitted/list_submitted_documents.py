"""Use-case: list submitted documents via supabase port."""
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(
    supabase: SupabaseNamespacedPort,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    sort_by: str = "date",
    sort_dir: str = "desc",
    shop_domain: str | None = None,
) -> list[dict]:
    return supabase.submitted.list_submitted_documents(
        limit=limit,
        offset=offset,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
        shop_domain=shop_domain,
    )
