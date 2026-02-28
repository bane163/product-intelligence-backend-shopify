"""Use-case: bulk delete submitted documents via supabase port."""

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
    for submitted_id in ids:
        if supabase.submitted.delete_submitted_document(
            submitted_id,
            shop_domain=shop_domain,
        ):
            deleted_ids.append(submitted_id)
        else:
            failed_ids.append(submitted_id)
    return {"deleted_ids": deleted_ids, "failed_ids": failed_ids}
