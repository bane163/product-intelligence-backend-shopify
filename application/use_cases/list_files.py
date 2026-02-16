from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, limit: int = 100, offset: int = 0):
    return supabase.list_files(limit=limit, offset=offset)
