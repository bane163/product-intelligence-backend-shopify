from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, ids: list[str]) -> dict[str, list[str]]:
    deleted_ids: list[str] = []
    failed_ids: list[str] = []
    for file_id in ids:
        if supabase.delete_file(file_id):
            deleted_ids.append(file_id)
        else:
            failed_ids.append(file_id)
    return {"deleted_ids": deleted_ids, "failed_ids": failed_ids}
