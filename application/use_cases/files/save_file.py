"""Use-case: save a file via supabase port."""
from application.ports.supabase_port import SupabasePort


def execute(
    supabase: SupabasePort,
    file_id: str,
    name: str,
    content: bytes,
    content_type: str | None = None,
    file_origin: str | None = None,
) -> None:
    return supabase.save_file(
        file_id,
        name=name,
        content=content,
        content_type=content_type,
        file_origin=file_origin,
    )
