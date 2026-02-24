"""Use-case: create an excel resume file from a product draft and save via supabase."""

import uuid
from typing import Any

from ai.excel_writer import create_excel_bytes
from ai.models import ProductsList
from application.ports.supabase_port import SupabaseNamespacedPort


def _optional_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def execute(supabase: SupabaseNamespacedPort, draft_id: str) -> dict[str, str]:
    draft = supabase.drafts.get_product_draft(draft_id)
    if not draft:
        raise LookupError("Draft not found")
    products_raw = draft.get("products")
    if not isinstance(products_raw, list) or not products_raw:
        raise ValueError("Draft has no products")
    if not all(isinstance(product, dict) for product in products_raw):
        raise ValueError("Draft products are invalid")
    products: list[dict[str, object]] = products_raw

    # If there's already an output file recorded, try to reuse it
    existing_output_file_id = _optional_str(draft, "output_file_id")
    existing_output_filename = _optional_str(draft, "output_filename")
    if existing_output_file_id:
        existing_file = supabase.file.get_file(existing_output_file_id)
        if existing_file:
            existing_name = existing_file.get("name")
            resolved_name = (
                existing_output_filename
                or (existing_name if isinstance(existing_name, str) and existing_name else None)
                or f"draft-{draft_id[:8]}.xlsx"
            )
            return {"file_id": existing_output_file_id, "filename": resolved_name}

    parsed = ProductsList.model_validate({"products": products})
    output_bytes = create_excel_bytes(parsed)
    file_id = str(uuid.uuid4())
    filename = f"draft-{draft_id[:8]}.xlsx"
    supabase.file.save_file(
        file_id=file_id,
        name=filename,
        content=output_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_origin="draft_resume",
    )
    # update the draft with output file info
    from application.use_cases.drafts.save_product_draft import (
        execute as save_product_draft_execute,
    )

    save_product_draft_execute(
        supabase=supabase,
        draft_id=draft_id,
        run_id=_optional_str(draft, "run_id"),
        import_mode=_optional_str(draft, "import_mode") or "auto",
        draft_name=_optional_str(draft, "draft_name"),
        input_file_id=_optional_str(draft, "input_file_id"),
        input_filename=_optional_str(draft, "input_filename"),
        output_file_id=file_id,
        output_filename=filename,
        products=products,
    )
    return {"file_id": file_id, "filename": filename}
