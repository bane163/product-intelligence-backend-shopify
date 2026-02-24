"""Use-case: get a product draft via supabase port."""
from typing import Any
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(supabase: SupabaseNamespacedPort, draft_id: str) -> dict[str, Any] | None:
    return supabase.drafts.get_product_draft(draft_id)
