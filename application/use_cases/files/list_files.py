from application.ports.supabase_port import SupabaseNamespacedPort


def execute(supabase: SupabaseNamespacedPort, limit: int = 100, offset: int = 0):
    return supabase.file.list_files(limit=limit, offset=offset)
