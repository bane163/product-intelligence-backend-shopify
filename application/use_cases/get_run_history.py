"""Use-case: get run history via supabase port."""

from typing import Any
from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, run_id: str) -> dict[str, Any]:
    return supabase.get_run_history(run_id)
