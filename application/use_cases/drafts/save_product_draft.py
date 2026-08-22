from typing import Any, Dict, List

from application.ports.supabase_port import SupabaseNamespacedPort


def execute(
    supabase: SupabaseNamespacedPort,
    *,
    draft_id: str,
    run_id: str | None,
    import_mode: str,
    draft_name: str | None,
    shop_domain: str | None = None,
    input_file_id: str | None = None,
    input_filename: str | None = None,
    extraction_status: str | None = None,
    extraction_run_id: str | None = None,
    extraction_error: str | None = None,
    quality_gate_status: str | None = None,
    quality_report: dict[str, Any] | None = None,
    quality_checked_at: str | None = None,
    submit_status: str | None = None,
    submit_run_id: str | None = None,
    submit_error: str | None = None,
    require_lifecycle_columns: bool = False,
    ai_enhancements_enabled: bool | None = None,
    revision: int | None = None,
    products: List[Dict[str, object]] | None = None,
) -> Dict[str, object]:
    resolved_ai_enhancements_enabled = ai_enhancements_enabled
    if resolved_ai_enhancements_enabled is None:
        existing = supabase.drafts.get_product_draft(
            draft_id, shop_domain=shop_domain
        )
        existing_setting = (
            existing.get("ai_enhancements_enabled")
            if isinstance(existing, dict)
            else None
        )
        resolved_ai_enhancements_enabled = (
            existing_setting if isinstance(existing_setting, bool) else True
        )
    return supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=run_id,
        import_mode=import_mode,
        draft_name=draft_name,
        shop_domain=shop_domain,
        input_file_id=input_file_id,
        input_filename=input_filename,
        extraction_status=extraction_status,
        extraction_run_id=extraction_run_id,
        extraction_error=extraction_error,
        quality_gate_status=quality_gate_status,
        quality_report=quality_report,
        quality_checked_at=quality_checked_at,
        submit_status=submit_status,
        submit_run_id=submit_run_id,
        submit_error=submit_error,
        require_lifecycle_columns=require_lifecycle_columns,
        ai_enhancements_enabled=resolved_ai_enhancements_enabled,
        revision=revision,
        products=products or [],
    )
