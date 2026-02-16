"""Use-case: get a submitted document via supabase port."""
from typing import Any
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, submitted_id: str) -> dict[str, Any] | None:
    return supabase.get_submitted_document(submitted_id)
