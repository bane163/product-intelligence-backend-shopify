"""Use-case: list submitted documents via supabase port."""
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(supabase: SupabaseNamespacedPort, limit=50, offset=0, search=None, sort_by="date", sort_dir="desc") -> list[dict]:
    return supabase.submitted.list_submitted_documents(limit=limit, offset=offset, search=search, sort_by=sort_by, sort_dir=sort_dir)
