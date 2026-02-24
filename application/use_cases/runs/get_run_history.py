"""Use-case: get run history via supabase port."""

from typing import Any
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(
    supabase: SupabaseNamespacedPort,
    run_id: str,
    *,
    shop_domain: str | None = None,
) -> dict[str, Any]:
    return supabase.runs.get_run_history(run_id, shop_domain=shop_domain)
