"""Use-case: delete a submitted document via supabase port."""
from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, submitted_id: str) -> bool:
    return supabase.delete_submitted_document(submitted_id)
