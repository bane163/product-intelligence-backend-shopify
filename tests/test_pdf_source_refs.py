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


def test_pdf_reconciles_unique_legacy_default_variant_sku_and_numeric_price_paths():
    products = [{
        "title": "Studio Mixer",
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [{
            "option1": "Default Title", "sku": "SMX-100", "price": "89.99",
        }],
        "source_refs": [
            {"field": "sku", "value_path": "variant[Default Title].sku", "anchor_id": "sku"},
            {"field": "price", "value_path": "variant[Default Title].price", "anchor_id": "price"},
        ],
    }]
    layout = DocumentLayout(1, (
        anchor("title", "Studio Mixer", 0),
        anchor("sku", "SKU: SMX-100", 10),
        anchor("price", "$89.99", 20),
    ), "test", (1,), (1,))

    verify_pdf_source_refs(products, layout, "source-file")
    by_field = {ref["field"]: ref for ref in products[0]["source_refs"]}

    assert by_field["sku"]["value_path"] == "variant[title-default-title].sku"
    assert by_field["price"]["value_path"] == "variant[title-default-title].price"
    assert by_field["price"]["anchor_id"] == "price"


def test_pdf_does_not_reconcile_wrong_duplicate_or_ambiguous_legacy_claims():
    duplicate = {
        "title": "Studio Mixer",
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [{"option1": "Default Title", "sku": "SMX-100"}],
        "source_refs": [
            {"field": "sku", "value_path": "variant[Default Title].sku", "anchor_id": "sku"},
            {"field": "sku", "value_path": "variant[Default Title].sku", "anchor_id": "sku"},
        ],
    }
    wrong = {
        "title": "Other Mixer",
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [{"option1": "Default Title", "sku": "OTHER-100"}],
        "source_refs": [
            {"field": "sku", "value_path": "variant[Default Title].sku", "anchor_id": "wrong"},
        ],
    }
    ambiguous = {
        "title": "Two Pack",
        "options": [{"name": "Size", "values": ["S", "M"]}],
        "variants": [
            {"option1": "S", "sku": "SHARED"},
            {"option1": "M", "sku": "SHARED"},
        ],
        "source_refs": [
            {"field": "sku", "value_path": "variant[Default Title].sku", "anchor_id": "shared"},
        ],
    }
    layout = DocumentLayout(1, (
        anchor("sku", "SMX-100", 0),
        anchor("wrong", "NOT-OTHER", 10),
        anchor("shared", "SHARED", 20),
    ), "test", (1,), (1,))

    verify_pdf_source_refs([duplicate, wrong, ambiguous], layout, "source-file")

    assert not any(ref["field"] == "sku" for ref in duplicate["source_refs"])
    assert not any(ref["field"] == "sku" for ref in wrong["source_refs"])
    assert not any(ref["field"] == "sku" for ref in ambiguous["source_refs"])
