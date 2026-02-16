"""Use-case: list submitted documents via supabase port."""
from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, limit=50, offset=0, search=None, sort_by="date", sort_dir="desc") -> list[dict]:
    return supabase.list_submitted_documents(limit=limit, offset=offset, search=search, sort_by=sort_by, sort_dir=sort_dir)
