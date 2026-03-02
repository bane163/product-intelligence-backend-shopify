"""Use-case: bulk delete product drafts via supabase port."""

from typing import Iterable

from application.ports.supabase_port import SupabaseNamespacedPort
from application.use_cases._bulk_delete import collect_bulk_delete_results


def execute(
    supabase: SupabaseNamespacedPort,
    ids: Iterable[str],
    *,
    shop_domain: str | None = None,
) -> dict[str, list[str]]:
    return collect_bulk_delete_results(
        ids,
        lambda draft_id: supabase.drafts.delete_product_draft(
            draft_id,
            shop_domain=shop_domain,
        ),
    )
