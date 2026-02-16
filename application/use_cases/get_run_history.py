"""Use-case: get run history via supabase port."""

from typing import Any
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, run_id: str) -> dict[str, Any]:
    return supabase.get_run_history(run_id)
