"""Use-case: bulk delete product drafts via supabase port."""

from typing import Iterable

from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, ids: Iterable[str]) -> dict[str, list[str]]:
    deleted_ids = []
    failed_ids = []
    for draft_id in ids:
        if supabase.delete_product_draft(draft_id):
            deleted_ids.append(draft_id)
        else:
            failed_ids.append(draft_id)
    return {"deleted_ids": deleted_ids, "failed_ids": failed_ids}
