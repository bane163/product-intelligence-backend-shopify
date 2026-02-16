"""Use-case to retrieve a file via the supabase port."""

from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, file_id: str) -> dict[str, object] | None:
    """Return file data dict or None. Thin wrapper around the supabase port."""
    return supabase.get_file(file_id)
