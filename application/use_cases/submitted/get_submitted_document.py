"""Use-case: get a submitted document via supabase port."""
from typing import Any
from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, submitted_id: str) -> dict[str, Any] | None:
    return supabase.get_submitted_document(submitted_id)
