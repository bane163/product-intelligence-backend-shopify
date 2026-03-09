"""Use-case: delete a run by id via supabase port."""

from application.ports.supabase_port import SupabaseNamespacedPort


def execute(
    supabase: SupabaseNamespacedPort,
    run_id: str,
    *,
    shop_domain: str | None = None,
) -> bool:
    return supabase.runs.delete_run(run_id, shop_domain=shop_domain)
