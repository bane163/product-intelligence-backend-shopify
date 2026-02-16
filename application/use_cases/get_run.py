"""Use-case: get a run by id via supabase port."""
from typing import Any
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, run_id: str) -> dict[str, Any] | None:
    return supabase.get_run(run_id)
