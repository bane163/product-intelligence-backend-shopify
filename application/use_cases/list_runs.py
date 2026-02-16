"""Use-case: list runs via supabase port."""
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, limit: int = 50, offset: int = 0, status: str | None = None) -> list[dict]:
    return supabase.list_runs(limit=limit, offset=offset, status=status)
