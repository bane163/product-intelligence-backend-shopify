"""Use-case to retrieve a file via the supabase port."""

from application.ports.supabase_port import SupabaseNamespacedPort


def execute(supabase: SupabaseNamespacedPort, file_id: str, shop_domain: str | None = None) -> dict[str, object] | None:
    """Return file data dict or None. Thin wrapper around the supabase port."""
    result = supabase.file.get_file(file_id)
    if not result:
        return None
    if shop_domain is not None and str(result.get("shop_domain") or "").strip().lower() != shop_domain.strip().lower():
        return None
    return result
