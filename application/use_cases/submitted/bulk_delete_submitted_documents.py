"""Use-case: bulk delete submitted documents via supabase port."""

from typing import Iterable
from application.ports.supabase_port import SupabasePort


def execute(
    supabase: SupabasePort, ids: Iterable[str]
) -> dict[str, list[str]]:
    deleted_ids = []
    failed_ids = []
    for submitted_id in ids:
        if supabase.delete_submitted_document(submitted_id):
            deleted_ids.append(submitted_id)
        else:
            failed_ids.append(submitted_id)
    return {"deleted_ids": deleted_ids, "failed_ids": failed_ids}
