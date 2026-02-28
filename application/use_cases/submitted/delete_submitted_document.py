"""Use-case: delete a submitted document via supabase port."""
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(
    supabase: SupabaseNamespacedPort,
    submitted_id: str,
    *,
    shop_domain: str | None = None,
) -> bool:
    return supabase.submitted.delete_submitted_document(
        submitted_id,
        shop_domain=shop_domain,
    )
