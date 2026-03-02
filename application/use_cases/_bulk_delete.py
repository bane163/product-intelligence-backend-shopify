"""Shared bulk-delete helper for use-case modules."""

from collections.abc import Callable, Iterable


def collect_bulk_delete_results(
    ids: Iterable[str],
    delete_one: Callable[[str], bool],
) -> dict[str, list[str]]:
    deleted_ids: list[str] = []
    failed_ids: list[str] = []
    for item_id in ids:
        if delete_one(item_id):
            deleted_ids.append(item_id)
        else:
            failed_ids.append(item_id)
    return {"deleted_ids": deleted_ids, "failed_ids": failed_ids}
