"""Use-case: bulk delete product drafts via supabase port."""

from typing import Iterable

from application.ports.supabase_port import SupabaseNamespacedPort


def execute(
    supabase: SupabaseNamespacedPort,
    ids: Iterable[str],
    *,
    shop_domain: str | None = None,
) -> dict[str, list[str]]:
    deleted_ids = []
    failed_ids = []
    for draft_id in ids:
        if supabase.drafts.delete_product_draft(draft_id, shop_domain=shop_domain):
            deleted_ids.append(draft_id)
        else:
            failed_ids.append(draft_id)
    return {"deleted_ids": deleted_ids, "failed_ids": failed_ids}
