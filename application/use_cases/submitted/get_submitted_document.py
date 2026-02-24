"""Use-case: get a submitted document via supabase port."""
from typing import Any
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(supabase: SupabaseNamespacedPort, submitted_id: str) -> dict[str, Any] | None:
    return supabase.submitted.get_submitted_document(submitted_id)
