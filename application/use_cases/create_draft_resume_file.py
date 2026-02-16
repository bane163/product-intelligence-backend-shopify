"""Use-case: create an excel resume file from a product draft and save via supabase."""

import uuid
from ai.excel_writer import create_excel_bytes
from ai.models import ProductsList
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, draft_id: str) -> dict[str, str]:
    draft = supabase.get_product_draft(draft_id)
    if not draft:
        raise LookupError("Draft not found")
    products = draft.get("products")
    if not isinstance(products, list) or not products:
        raise ValueError("Draft has no products")

    # If there's already an output file recorded, try to reuse it
    existing_output_file_id = draft.get("output_file_id")
    existing_output_filename = draft.get("output_filename")
    if isinstance(existing_output_file_id, str) and existing_output_file_id:
        existing_file = supabase.get_file(existing_output_file_id)
        if existing_file:
            resolved_name = (
                existing_output_filename
                if isinstance(existing_output_filename, str)
                and existing_output_filename
                else existing_file.get("name") or f"draft-{draft_id[:8]}.xlsx"
            )
            return {"file_id": existing_output_file_id, "filename": resolved_name}

    parsed = ProductsList.model_validate({"products": products})
    output_bytes = create_excel_bytes(parsed)
    file_id = str(uuid.uuid4())
    filename = f"draft-{draft_id[:8]}.xlsx"
    supabase.save_file(
        file_id=file_id,
        name=filename,
        content=output_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    # update the draft with output file info
    from application.use_cases.save_product_draft import (
        execute as save_product_draft_execute,
    )

    save_product_draft_execute(
        supabase=supabase,
        draft_id=draft_id,
        run_id=draft.get("run_id") if isinstance(draft.get("run_id"), str) else None,
        import_mode=(
            draft.get("import_mode")
            if isinstance(draft.get("import_mode"), str) and draft.get("import_mode")
            else "auto"
        ),
        draft_name=(
            draft.get("draft_name")
            if isinstance(draft.get("draft_name"), str)
            else None
        ),
        input_file_id=(
            draft.get("input_file_id")
            if isinstance(draft.get("input_file_id"), str)
            else None
        ),
        input_filename=(
            draft.get("input_filename")
            if isinstance(draft.get("input_filename"), str)
            else None
        ),
        output_file_id=file_id,
        output_filename=filename,
        products=products,
    )
    return {"file_id": file_id, "filename": filename}
