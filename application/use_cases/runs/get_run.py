"""Use-case: get a run by id via supabase port."""
from typing import Any
from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, run_id: str) -> dict[str, Any] | None:
    return supabase.get_run(run_id)
