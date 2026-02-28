"""Use-case: delete a product draft via supabase port."""
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(
    supabase: SupabaseNamespacedPort,
    draft_id: str,
    *,
    shop_domain: str | None = None,
) -> bool:
    return supabase.drafts.delete_product_draft(draft_id, shop_domain=shop_domain)
