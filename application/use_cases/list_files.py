from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, limit: int = 100, offset: int = 0):
    return supabase.list_files(limit=limit, offset=offset)
