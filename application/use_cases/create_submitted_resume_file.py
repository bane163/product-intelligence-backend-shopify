"""Use-case: create an excel resume file from a submitted document and save via supabase."""
import uuid
from ai.excel_writer import create_excel_bytes
from ai.models import ProductsList
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, submitted_id: str) -> dict[str, str]:
    document = supabase.get_submitted_document(submitted_id)
    if not document:
        raise LookupError("Submitted document not found")
    products = document.get("products")
    if not isinstance(products, list) or not products:
        raise ValueError("Submitted document has no products")
    parsed = ProductsList.model_validate({"products": products})
    output_bytes = create_excel_bytes(parsed)
    file_id = str(uuid.uuid4())
    filename = f"submitted-{submitted_id[:8]}.xlsx"
    supabase.save_file(
        file_id=file_id,
        name=filename,
        content=output_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    return {"file_id": file_id, "filename": filename}
