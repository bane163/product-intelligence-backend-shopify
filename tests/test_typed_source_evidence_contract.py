import io

import pytest

from openpyxl import Workbook

from application.domain.review_value_paths import iter_review_values, values_equal
from application.domain.shopify_mutation_plan import merge_structured_product, review_changes
from application.ports.document_layout_port import DocumentAnchor, DocumentLayout
from application.services.pdf_source_refs import verify_pdf_source_refs
from application.services.spreadsheet_source_refs import enrich_spreadsheet_source_refs


def _workbook(rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Products"
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _product():
    return {
        "title": "Planter",
        "tags": ["Home", "Sale"],
        "options": [{"name": "Size", "values": ["Small", "Large"]}],
        "variants": [{"option1": "Small", "sku": "P-S", "price": 0, "taxable": False}],
        "metafields": [{
            "namespace": "custom", "key": "details",
            "value": '{"material":"ceramic","dimensions":[4,6]}', "type": "json",
        }],
    }


def test_spreadsheet_evidence_is_type_aware_and_canonical():
    product = _product()
    data = _workbook([[
        "Title", "Tags", "SKU", "Price", "Taxable", "option[size].values",
        "metafield[custom-details].value", "metafield[custom-details].type",
    ], [
        "Planter", "Home, Sale", "P-S", 0, "false", '["Small", "Large"]',
        '{"dimensions": [4, 6], "material": "ceramic"}', "json",
    ]])

    enrich_spreadsheet_source_refs([product], data, "products.xlsx", "file-1")
    by_path = {ref["value_path"]: ref for ref in product["source_refs"]}

    assert by_path["tags"]["cell"] == "B2"
    assert by_path["variant[size-small].price"]["cell"] == "D2"
    assert by_path["variant[size-small].taxable"]["cell"] == "E2"
    assert by_path["option[size].values"]["cell"] == "F2"
    assert by_path["metafield[custom-details].value"]["cell"] == "G2"
    assert all(ref["source_file_id"] == "file-1" for ref in by_path.values())


def test_list_order_mismatch_and_non_explicit_boolean_are_omitted():
    product = _product()
    data = _workbook([[
        "Title", "SKU", "Taxable", "option[size].values",
    ], ["Planter", "P-S", "off", '["Large", "Small"]']])

    enrich_spreadsheet_source_refs([product], data, "products.xlsx")
    paths = {ref["value_path"] for ref in product["source_refs"]}

    assert "variant[size-small].taxable" not in paths
    assert "option[size].values" not in paths


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("true", True), ("yes", True), ("1", True), (1, True),
        ("false", False), ("no", False), ("0", False), (0, False),
    ],
)
def test_explicit_boolean_tokens_are_verified_as_source_evidence(source, expected):
    product = {
        "title": f"Boolean {source!s}-{expected}",
        "variants": [{"sku": f"SKU-{source!s}-{expected}", "taxable": expected}],
    }
    data = _workbook([
        ["Title", "SKU", "Taxable"],
        [product["title"], product["variants"][0]["sku"], source],
    ])

    enrich_spreadsheet_source_refs([product], data, "products.xlsx")

    ref = next(ref for ref in product["source_refs"] if ref["value_path"].endswith(".taxable"))
    assert ref["cell"] == "C2"
    assert values_equal(source, expected)


def test_snake_case_variant_aliases_emit_one_canonical_path_and_resolve_cells():
    product = {
        "title": "Alias product",
        "options": [{"name": "Size", "values": ["Small"]}],
        "variants": [{
            "option1": "Small", "sku": "ALIAS-S",
            "compare_at_price": "19.99", "tax_code": "PC040100",
            "inventory_policy": "continue", "cost_per_item": "7.50",
            "tracked": True, "requires_shipping": False,
            "weight": "1.25", "weight_unit": "kg",
        }],
    }
    variant_values = [value for value in iter_review_values(product) if value.scope == "variant"]
    paths = [value.value_path for value in variant_values]
    expected_paths = {
        "variant[size-small].sku",
        "variant[size-small].compareAtPrice",
        "variant[size-small].taxCode",
        "variant[size-small].inventoryPolicy",
        "variant[size-small].inventoryItem.cost",
        "variant[size-small].inventoryItem.tracked",
        "variant[size-small].inventoryItem.requiresShipping",
        "variant[size-small].inventoryItem.weight.value",
        "variant[size-small].inventoryItem.weight.unit",
    }
    assert set(paths) == expected_paths
    assert len(paths) == len(set(paths))

    headers = ["Title", *sorted(expected_paths)]
    by_path = {value.value_path: value.value for value in variant_values}
    data = _workbook([headers, ["Alias product", *(by_path[path] for path in sorted(expected_paths))]])

    enrich_spreadsheet_source_refs([product], data, "products.xlsx")
    resolved = {ref["value_path"] for ref in product["source_refs"]}

    assert expected_paths <= resolved

    duplicate_alias_product = {**product, "variants": [{
        **product["variants"][0],
        "compareAtPrice": "19.99", "taxCode": "PC040100",
        "inventoryPolicy": "continue", "cost": "7.50",
        "requiresShipping": False, "weightUnit": "kg",
    }]}
    duplicate_paths = [
        value.value_path for value in iter_review_values(duplicate_alias_product)
        if value.scope == "variant"
    ]
    assert set(duplicate_paths) == expected_paths
    assert len(duplicate_paths) == len(set(duplicate_paths))


def test_snake_case_alias_evidence_survives_canonical_mutation_rows():
    imported = {
        "title": "Alias plan",
        "options": [{"name": "Size", "values": ["Small"]}],
        "variants": [{
            "option1": "Small", "sku": "ALIAS-S",
            "compare_at_price": "19.99", "tax_code": "PC040100",
            "inventory_policy": "continue", "cost_per_item": "7.50",
            "tracked": True, "requires_shipping": False,
            "weight": "1.25", "weight_unit": "kg",
        }],
    }
    variant_values = [value for value in iter_review_values(imported) if value.scope == "variant"]
    imported["source_refs"] = [
        {"field": value.field, "value_path": value.value_path, "sheet": "Products", "cell": f"A{index}"}
        for index, value in enumerate(variant_values, 2)
    ]
    proposed, blockers, unapplied = merge_structured_product(None, imported, {"title": imported["title"]})

    rows = review_changes(None, imported, proposed, blockers, unapplied)
    by_path = {row["value_path"]: row for row in rows}
    expected_paths = {value.value_path for value in variant_values}

    assert expected_paths <= by_path.keys()
    assert all(by_path[path]["origin"] == "source" for path in expected_paths)
    assert all("source_ref" in by_path[path] for path in expected_paths)


def test_mismatched_or_duplicate_exact_claims_are_not_repaired_incidentally():
    product = _product()
    product["source_refs"] = [
        {"field": "taxable", "value_path": "variant[size-small].taxable", "sheet": "Products", "cell": "A2"},
        {"field": "price", "value_path": "variant[size-small].price", "sheet": "Products", "cell": "D2"},
        {"field": "price", "value_path": "variant[size-small].price", "sheet": "Products", "cell": "D2"},
    ]
    data = _workbook([
        ["Title", "SKU", "Taxable", "Price"],
        ["Planter", "P-S", "false", 0],
    ])

    enrich_spreadsheet_source_refs([product], data, "products.xlsx")
    paths = {ref["value_path"] for ref in product["source_refs"]}

    assert "variant[size-small].taxable" not in paths
    assert "variant[size-small].price" not in paths


def test_structured_parent_cell_promotes_only_canonical_child_membership():
    product = _product()
    product["source_refs"] = [{"field": "metafields", "sheet": "Products", "cell": "B2"}]
    data = _workbook([
        ["Title", "Metafields"],
        ["Planter", '[{"namespace":"custom","key":"details","type":"json","value":"{\\"dimensions\\":[4,6],\\"material\\":\\"ceramic\\"}"}]'],
    ])

    enrich_spreadsheet_source_refs([product], data, "products.xlsx")
    by_path = {ref["value_path"]: ref for ref in product["source_refs"]}

    assert by_path["metafield[custom-details].value"]["cell"] == "B2"
    assert by_path["metafield[custom-details].type"]["cell"] == "B2"


def test_pdf_resolves_typed_paths_and_suppresses_explicit_derived_values():
    product = _product()
    product["value_origins"] = {"metafields[custom.details].value": "derived"}
    anchors = (
        DocumentAnchor("title", "source.pdf", 1, (0, 0, 10, 10), "Planter", "text", "test"),
        DocumentAnchor("price", "source.pdf", 1, (0, 10, 10, 20), "$0", "text", "test"),
        DocumentAnchor("taxable", "source.pdf", 1, (0, 20, 10, 30), "false", "text", "test"),
        DocumentAnchor("tags", "source.pdf", 1, (0, 30, 10, 40), '["Home", "Sale"]', "text", "test"),
        DocumentAnchor("option", "source.pdf", 1, (0, 40, 10, 50), "Small, Large", "text", "test"),
        DocumentAnchor("metafield", "source.pdf", 1, (0, 50, 10, 60), '{"dimensions":[4,6],"material":"ceramic"}', "text", "test"),
    )
    layout = DocumentLayout(1, anchors, "test", (1,), (1,))

    verify_pdf_source_refs([product], layout, "file-1")
    paths = {ref["value_path"] for ref in product["source_refs"]}

    assert {"tags", "variant[size-small].price", "variant[size-small].taxable", "option[size].values"} <= paths
    assert "metafield[custom-details].value" not in paths


def test_typed_exact_source_refs_survive_mutation_plan_decoration():
    imported = _product()
    imported["source_refs"] = [
        {"field": "values", "value_path": "options[Size].values", "sheet": "Products", "cell": "F2"},
        {"field": "taxable", "value_path": "variants[Size: Small].taxable", "sheet": "Products", "cell": "E2"},
        {"field": "value", "value_path": "metafields[custom.details].value", "sheet": "Products", "cell": "G2"},
    ]
    proposed, blockers, unapplied = merge_structured_product(None, imported, {"title": "Planter", "tags": []})

    rows = review_changes(None, imported, proposed, blockers, unapplied)
    by_path = {row["value_path"]: row for row in rows}

    assert by_path["option[size].values"]["source_ref"]["cell"] == "F2"
    assert by_path["variant[size-small].taxable"]["source_ref"]["cell"] == "E2"
    assert by_path["metafield[custom-details].value"]["source_ref"]["cell"] == "G2"


def test_recovered_default_variant_refs_decorate_sku_and_price_as_sourced():
    imported = {
        "title": "Studio Mixer",
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [{
            "option1": "Default Title", "sku": "SMX-100", "price": "89.99",
        }],
        "value_origins": {
            "variant[title-default-title].sku": "source",
            "variant[title-default-title].price": "source",
        },
        "source_refs": [
            {"field": "sku", "value_path": "variant[Default Title].sku", "anchor_id": "sku"},
            {"field": "price", "value_path": "variant[Default Title].price", "anchor_id": "price"},
        ],
    }
    layout = DocumentLayout(1, (
        DocumentAnchor("title", "source.pdf", 1, (0, 0, 10, 10), "Studio Mixer", "text", "test"),
        DocumentAnchor("sku", "source.pdf", 1, (0, 10, 10, 20), "SMX-100", "text", "test"),
        DocumentAnchor("price", "source.pdf", 1, (0, 20, 10, 30), "$89.99", "text", "test"),
    ), "test", (1,), (1,))
    verify_pdf_source_refs([imported], layout, "file-1")

    proposed, blockers, unapplied = merge_structured_product(
        None, imported, {"title": "Studio Mixer", "tags": []}
    )
    rows = review_changes(None, imported, proposed, blockers, unapplied)
    by_path = {row["value_path"]: row for row in rows}

    for path in (
        "variant[title-default-title].sku",
        "variant[title-default-title].price",
    ):
        assert by_path[path]["origin"] == "source"
        assert by_path[path]["source_ref"]["document_kind"] == "pdf"
