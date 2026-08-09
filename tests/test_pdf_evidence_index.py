from application.ports.document_layout_port import DocumentAnchor, DocumentLayout
from application.services.pdf_evidence_index import (
    blank_unverified_optional_pdf_values,
    build_evidence_catalog,
    resolve_indexed_pdf_source_refs,
    validate_indexed_pdf_evidence,
)
from application.domain.field_derivation_policy import apply_server_owned_origins


def anchor(
    anchor_id: str,
    text: str,
    bbox: tuple[float, float, float, float],
    role: str = "line",
    page: int = 1,
) -> DocumentAnchor:
    return DocumentAnchor(anchor_id, "source.pdf", page, bbox, text, role, "azure")


def layout(*anchors: DocumentAnchor) -> DocumentLayout:
    pages = tuple(sorted({item.page for item in anchors}))
    return DocumentLayout(max(pages, default=0), tuple(anchors), "azure", pages, pages)


def test_catalog_prefers_cells_then_lines_then_only_uncovered_words():
    source = layout(
        anchor("cell", "SKU UCT-001", (0.1, 0.1, 0.5, 0.2), "table_cell"),
        anchor("cell-line", "SKU UCT-001", (0.1, 0.1, 0.5, 0.2)),
        anchor("cell-word", "UCT-001", (0.25, 0.12, 0.4, 0.18), "word"),
        anchor("line", "Price 19.99", (0.1, 0.4, 0.5, 0.5)),
        anchor("line-word", "19.99", (0.3, 0.42, 0.45, 0.48), "word"),
        anchor("word", "Uncovered", (0.1, 0.7, 0.3, 0.75), "word"),
    )

    catalog = build_evidence_catalog(source)

    assert [(entry.evidence_index, entry.anchor_id) for entry in catalog.entries] == [
        (1, "cell"),
        (2, "line"),
        (3, "word"),
    ]
    assert catalog.to_prompt_text() == "1. SKU UCT-001\n2. Price 19.99\n3. Uncovered"


def test_catalog_numbering_is_deterministic_by_page_position_not_input_order():
    anchors = (
        anchor("p2", "Second page", (0.1, 0.1, 0.3, 0.2), page=2),
        anchor("right", "Right", (0.6, 0.2, 0.8, 0.3)),
        anchor("left", "Left", (0.1, 0.2, 0.3, 0.3)),
    )

    forward = build_evidence_catalog(layout(*anchors))
    reverse = build_evidence_catalog(layout(*reversed(anchors)))

    assert [entry.anchor_id for entry in forward.entries] == ["left", "right", "p2"]
    assert forward == reverse


def test_valid_index_is_authoritative_and_model_locations_are_ignored():
    catalog = build_evidence_catalog(
        layout(anchor("cropped-title", "lassic Cotton T-Shirt", (0.2, 0.3, 0.8, 0.4)))
    )
    product = {
        "title": "Classic Cotton T-Shirt",
        "source_refs": [
            {
                "field": "title",
                "value_path": "title",
                "evidence_index": 1,
                "page": 99,
                "bbox": [9, 9, 9, 9],
                "anchor_id": "invented",
                "source_file_id": "invented-file",
            }
        ],
    }

    assert validate_indexed_pdf_evidence([product], catalog).valid
    assert resolve_indexed_pdf_source_refs([product], catalog, "trusted-file") == 1
    assert product["source_refs"] == [
        {
            "source_file_id": "trusted-file",
            "field": "title",
            "value_path": "title",
            "document_kind": "pdf",
            "anchor_id": "cropped-title",
            "page": 1,
            "bbox": [0.2, 0.3, 0.8, 0.4],
            "value": "Classic Cotton T-Shirt",
            "source_provider": "azure",
        }
    ]
    assert product["value_origins"]["title"] == "source"


def test_duplicate_visible_text_is_disambiguated_by_selected_index():
    catalog = build_evidence_catalog(
        layout(
            anchor("first", "Same title", (0.1, 0.1, 0.4, 0.2)),
            anchor("second", "Same title", (0.1, 0.6, 0.4, 0.7)),
        )
    )
    product = {
        "title": "Same title",
        "source_refs": [{"value_path": "title", "evidence_index": 2}],
    }

    assert validate_indexed_pdf_evidence([product], catalog).valid
    resolve_indexed_pdf_source_refs([product], catalog, "file")
    assert product["source_refs"][0]["anchor_id"] == "second"
    assert product["source_refs"][0]["bbox"] == [0.1, 0.6, 0.4, 0.7]


def test_feedback_names_missing_invalid_and_mismatched_canonical_paths():
    catalog = build_evidence_catalog(
        layout(
            anchor("title", "Mixer", (0.1, 0.1, 0.4, 0.2)),
            anchor("sku", "SMX-100", (0.1, 0.3, 0.4, 0.4)),
        )
    )
    product = {
        "title": "Mixer",
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [{"option1": "Default Title", "sku": "SMX-100", "price": 89.99}],
        "value_origins": {"option[title].values": "derived"},
        "source_refs": [
            {"value_path": "title", "evidence_index": 1},
            {"value_path": "variant[Default Title].sku", "evidence_index": None},
            {"value_path": "variant[wrong].barcode", "evidence_index": 2},
        ],
    }

    result = validate_indexed_pdf_evidence([product], catalog)

    assert not result.valid
    assert "variant[title-default-title].sku" in result.affected_paths
    assert "variant[title-default-title].price" in result.affected_paths
    assert "variant[wrong].barcode" in result.affected_paths
    assert all(path in result.feedback for path in result.affected_paths)


def test_default_variant_claims_resolve_to_canonical_paths_without_text_matching():
    catalog = build_evidence_catalog(
        layout(
            anchor("sku", "cropped-SMX", (0.1, 0.3, 0.4, 0.4)),
            anchor("price", "cropped-price", (0.1, 0.5, 0.4, 0.6)),
        )
    )
    product = {
        "title": "Studio Mixer",
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [{"option1": "Default Title", "sku": "SMX-100", "price": "89.99"}],
        "value_origins": {"title": "ai_assisted", "option[title].values": "defaulted"},
        "source_refs": [
            {
                "field": "sku",
                "value_path": "variant[Default Title].sku",
                "evidence_index": 1,
            },
            {
                "field": "price",
                "value_path": "variant[Default Title].price",
                "evidence_index": 2,
            },
        ],
    }

    assert validate_indexed_pdf_evidence([product], catalog).valid
    resolve_indexed_pdf_source_refs([product], catalog, "file")
    by_path = {ref["value_path"]: ref for ref in product["source_refs"]}
    assert set(by_path) == {
        "variant[title-default-title].sku",
        "variant[title-default-title].price",
    }
    assert product["value_origins"]["variant[title-default-title].sku"] == "source"
    assert product["value_origins"]["variant[title-default-title].price"] == "source"


def test_array_style_variant_claim_with_full_field_path_reconciles_when_unique():
    catalog = build_evidence_catalog(
        layout(anchor("sku", "UCT-001", (0.1, 0.3, 0.4, 0.4)))
    )
    product = {
        "title": "Tee",
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [{"option1": "Default Title", "sku": "UCT-001"}],
        "value_origins": {"title": "derived", "option[title].values": "defaulted"},
        "source_refs": [
            {
                "field": "variants[0].sku",
                "value_path": "variants[0].sku",
                "evidence_index": 1,
            }
        ],
    }

    assert validate_indexed_pdf_evidence([product], catalog).valid
    resolve_indexed_pdf_source_refs([product], catalog, "file")
    assert product["source_refs"][0]["value_path"] == "variant[title-default-title].sku"


def test_unverified_optional_cost_and_shipping_are_left_blank_before_validation():
    catalog = build_evidence_catalog(
        layout(
            anchor("title", "Tee", (0.1, 0.1, 0.4, 0.2)),
            anchor("sku", "UCT-001", (0.1, 0.3, 0.4, 0.4)),
            anchor("price", "19.99", (0.1, 0.5, 0.4, 0.6)),
        )
    )
    product = {
        "title": "Tee",
        "variants": [
            {
                "sku": "UCT-001",
                "price": 19.99,
                "cost_per_item": 7.5,
                "requires_shipping": False,
            }
        ],
        "source_refs": [
            {"value_path": "title", "evidence_index": 1},
            {"value_path": "variant[uct-001].sku", "evidence_index": 2},
            {"value_path": "variant[uct-001].price", "evidence_index": 3},
        ],
    }

    removed = blank_unverified_optional_pdf_values([product], catalog)

    assert removed == (
        "variant[uct-001].inventoryitem.cost",
        "variant[uct-001].inventoryitem.requiresshipping",
    )
    assert "cost_per_item" not in product["variants"][0]
    assert "requires_shipping" not in product["variants"][0]
    assert validate_indexed_pdf_evidence([product], catalog).valid


def test_indexed_optional_cost_is_preserved_while_unverified_shipping_is_blank():
    catalog = build_evidence_catalog(
        layout(
            anchor("title", "Tee", (0.1, 0.1, 0.4, 0.2)),
            anchor("sku", "UCT-001", (0.1, 0.3, 0.4, 0.4)),
            anchor("price", "19.99", (0.1, 0.5, 0.4, 0.6)),
            anchor("cost", "7.50", (0.1, 0.7, 0.4, 0.8)),
        )
    )
    product = {
        "title": "Tee",
        "variants": [
            {
                "sku": "UCT-001",
                "price": 19.99,
                "inventoryItem": {"cost": 7.5, "requiresShipping": True},
            }
        ],
        "source_refs": [
            {"value_path": "title", "evidence_index": 1},
            {"value_path": "variant[uct-001].sku", "evidence_index": 2},
            {"value_path": "variant[uct-001].price", "evidence_index": 3},
            {"value_path": "variant[uct-001].inventoryItem.cost", "evidence_index": 4},
        ],
    }

    removed = blank_unverified_optional_pdf_values([product], catalog)

    assert removed == ("variant[uct-001].inventoryitem.requiresshipping",)
    assert product["variants"][0]["inventoryItem"] == {"cost": 7.5}
    assert validate_indexed_pdf_evidence([product], catalog).valid


def test_all_unverified_commerce_sensitive_values_are_removed_on_final_response():
    catalog = build_evidence_catalog(
        layout(anchor("title", "Tee", (0.1, 0.1, 0.4, 0.2)))
    )
    product = {
        "title": "Tee",
        "variants": [{
            "sku": "MADE-UP",
            "barcode": "1234",
            "price": 19.99,
            "compare_at_price": 29.99,
            "inventory_quantity": 12,
            "inventory_policy": "deny",
            "taxable": True,
            "fulfillment_service": "manual",
            "grams": 500,
            "inventoryItem": {
                "cost": 7.5,
                "tracked": True,
                "requiresShipping": True,
            },
        }],
        "value_origins": {
            "title": "derived",
            "variant[made-up].sku": "ai_assisted",
            "variant[made-up].price": "defaulted",
        },
    }

    initial = validate_indexed_pdf_evidence([product], catalog)
    assert not initial.valid
    assert "variant[made-up].sku" in initial.affected_paths
    assert "variant[made-up].price" in initial.affected_paths

    removed = blank_unverified_optional_pdf_values([product], catalog)

    assert "variant[made-up].sku" in removed
    assert "variant[made-up].price" in removed
    assert product["variants"] == [{"inventoryItem": {}}]
    assert validate_indexed_pdf_evidence([product], catalog).valid


def test_server_owns_derived_handle_and_default_variant_scaffolding_origins():
    product = {
        "title": "Classic Cotton Tee",
        "handle": "classic-cotton-tee",
        "variants": [{"title": "Default Title", "sku": "TEE-1", "price": 20}],
        "source_refs": [
            {"value_path": "handle", "evidence_index": 1},
            {"value_path": "option[title].values", "evidence_index": 1},
            {"value_path": "option[title].name", "evidence_index": 1},
            {"value_path": "variant[tee-1].option1", "evidence_index": 1},
            {"value_path": "variant[title-default-title].title", "evidence_index": 1},
        ],
    }

    apply_server_owned_origins(product)

    assert product["value_origins"]["handle"] == "derived"
    assert product["value_origins"]["option[title].values"] == "defaulted"
    assert product["value_origins"]["variant[title-default-title].title"] == "defaulted"
    assert product["options"] == [{"name": "Title", "values": ["Default Title"]}]
    assert product["variants"][0]["option1"] == "Default Title"
    # Structural option/name refs are harmless and ignored by PDF validation;
    # refs for derived/defaulted review values are removed.
    assert product["source_refs"] == [
        {"value_path": "option[title].name", "evidence_index": 1},
        {"value_path": "variant[tee-1].option1", "evidence_index": 1},
    ]


def test_pdf_validation_ignores_non_review_option_name_and_option_slot_refs():
    catalog = build_evidence_catalog(
        layout(anchor("title", "Mixer", (0.1, 0.1, 0.4, 0.2)))
    )
    product = {
        "title": "Mixer",
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [{"title": "Default Title", "option1": "Default Title"}],
        "source_refs": [
            {"value_path": "title", "evidence_index": 1},
            {"value_path": "option[title].name", "evidence_index": 1},
            {"value_path": "variant[hm-011].option1", "evidence_index": 1},
        ],
    }

    apply_server_owned_origins(product)
    result = validate_indexed_pdf_evidence([product], catalog)

    assert result.valid, result.feedback
