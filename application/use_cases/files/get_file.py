"""Use-case to retrieve a file via the supabase port."""

from application.ports.supabase_port import SupabaseNamespacedPort


def execute(supabase: SupabaseNamespacedPort, file_id: str) -> dict[str, object] | None:
    """Return file data dict or None. Thin wrapper around the supabase port."""
    return supabase.file.get_file(file_id)
