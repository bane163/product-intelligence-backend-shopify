from application.ports.document_layout_port import DocumentAnchor, DocumentLayout
from application.services.pdf_source_refs import verify_pdf_source_refs


def anchor(anchor_id: str, text: str, y: float) -> DocumentAnchor:
    return DocumentAnchor(anchor_id, "source.pdf", 1, (0, y, 100, y + 10), text, "text", "test")


def test_pdf_variant_evidence_requires_a_unique_anchor_per_exact_value_path():
    products = [{
        "title": "Tee",
        "options": [{"name": "Size", "values": ["S", "M"]}],
        "variants": [
            {"option1": "S", "sku": "TEE-S", "price": 10},
            {"option1": "M", "sku": "TEE-M", "price": 12},
        ],
    }]
    layout = DocumentLayout(1, (
        anchor("title", "Tee", 0),
        anchor("sku-s", "TEE-S", 10), anchor("sku-m", "TEE-M", 20),
        anchor("price-10-a", "$10", 30), anchor("price-10-b", "$10", 40),
        anchor("price-12", "$12", 50),
    ), "test", (1,), (1,))

    verify_pdf_source_refs(products, layout, "source-file")
    refs = products[0]["source_refs"]

    assert next(ref for ref in refs if ref["value_path"] == "variant[size-m].price")["anchor_id"] == "price-12"
    assert not any(ref["value_path"] == "variant[size-s].price" for ref in refs)
    assert {ref["value_path"] for ref in refs if ref["field"] == "sku"} == {
        "variant[size-s].sku", "variant[size-m].sku",
    }
    assert all(ref["source_file_id"] == "source-file" for ref in refs)
