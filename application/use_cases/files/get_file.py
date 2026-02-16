"""Use-case to retrieve a file via the supabase port."""

from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, file_id: str) -> dict[str, object] | None:
    """Return file data dict or None. Thin wrapper around the supabase port."""
    return supabase.get_file(file_id)
