from application.ports.supabase_port import SupabaseNamespacedPort
from application.use_cases._bulk_delete import collect_bulk_delete_results


def execute(supabase: SupabaseNamespacedPort, ids: list[str]) -> dict[str, list[str]]:
    return collect_bulk_delete_results(ids, supabase.file.delete_file)
