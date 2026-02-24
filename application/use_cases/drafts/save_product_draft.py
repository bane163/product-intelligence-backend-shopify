import uuid
from typing import Dict, List
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(supabase: SupabaseNamespacedPort, *, draft_id: str, run_id: str | None, import_mode: str, draft_name: str | None, input_file_id: str | None = None, input_filename: str | None = None, output_file_id: str | None = None, output_filename: str | None = None, products: List[Dict[str, object]] = []) -> Dict[str, object]:
    return supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=run_id,
        import_mode=import_mode,
        draft_name=draft_name,
        input_file_id=input_file_id,
        input_filename=input_filename,
        output_file_id=output_file_id,
        output_filename=output_filename,
        products=products,
    )
