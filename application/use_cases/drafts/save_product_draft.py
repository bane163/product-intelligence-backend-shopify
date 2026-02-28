from typing import Dict, List

from application.ports.supabase_port import SupabaseNamespacedPort


def execute(
    supabase: SupabaseNamespacedPort,
    *,
    draft_id: str,
    run_id: str | None,
    import_mode: str,
    draft_name: str | None,
    input_file_id: str | None = None,
    input_filename: str | None = None,
    output_file_id: str | None = None,
    output_filename: str | None = None,
    extraction_status: str | None = None,
    extraction_run_id: str | None = None,
    extraction_error: str | None = None,
    submit_status: str | None = None,
    submit_run_id: str | None = None,
    submit_error: str | None = None,
    require_lifecycle_columns: bool = False,
    products: List[Dict[str, object]] | None = None,
) -> Dict[str, object]:
    return supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=run_id,
        import_mode=import_mode,
        draft_name=draft_name,
        input_file_id=input_file_id,
        input_filename=input_filename,
        output_file_id=output_file_id,
        output_filename=output_filename,
        extraction_status=extraction_status,
        extraction_run_id=extraction_run_id,
        extraction_error=extraction_error,
        submit_status=submit_status,
        submit_run_id=submit_run_id,
        submit_error=submit_error,
        require_lifecycle_columns=require_lifecycle_columns,
        products=products or [],
    )
