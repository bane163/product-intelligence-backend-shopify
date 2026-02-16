"""Use-case: list product drafts via supabase port."""
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, limit: int = 50, offset: int = 0, search: str | None = None, sort_by: str = "date", sort_dir: str = "desc") -> list[dict]:
    return supabase.list_product_drafts(limit=limit, offset=offset, search=search, sort_by=sort_by, sort_dir=sort_dir)
