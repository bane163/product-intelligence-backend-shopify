import io
from pathlib import Path

from openpyxl import Workbook

from application.services.spreadsheet_source_refs import enrich_spreadsheet_source_refs
from application.domain.shopify_mutation_plan import merge_structured_product, review_changes


def workbook_bytes(rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Products"
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def multisheet_workbook_bytes(sheets):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets:
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_adds_exact_refs_for_first_variant_and_normalizes_numeric_price():
    products = [{"title": "Lamp", "vendor": "Acme", "variants": [{"sku": "L-1", "price": "12.00"}]}]
    data = workbook_bytes([
        ["Product Name", "Vendor", "Variant SKU", "Retail Price"],
        ["Other", "Else", "X", 4],
        ["Lamp", "Acme", "L-1", 12],
    ])
    enrich_spreadsheet_source_refs(products, data, "products.xlsx")
    assert {(ref["field"], ref["cell"]) for ref in products[0]["source_refs"]} == {
        ("title", "A3"), ("vendor", "B3"), ("sku", "C3"), ("price", "D3")
    }


def test_plain_inventory_header_links_all_eight_counts_to_k2_through_k9_and_review_rows():
    counts = [50, 75, 60, 120, 30, 40, 35, 200]
    sizes = ["S", "S", "M", "M", "L", "L", "XL", "XL"]
    colors = ["Red", "Blue"] * 4
    variants = [
        {
            "option1": size,
            "option2": color,
            "sku": f"TEE-{size}-{color}",
            "price": 20,
            "inventory_quantity": quantity,
        }
        for size, color, quantity in zip(sizes, colors, counts)
    ]
    product = {
        "title": "Inventory tee",
        "options": [
            {"name": "Size", "values": ["S", "M", "L", "XL"]},
            {"name": "Color", "values": ["Red", "Blue"]},
        ],
        "variants": variants,
    }
    rows = [[
        "Title", "Option1 Name", "Option1 Value", "Option2 Name", "Option2 Value",
        "SKU", "Price", "Unused 1", "Unused 2", "Unused 3", "Inventory",
    ]]
    rows.extend([
        ["Inventory tee" if index == 0 else "", "Size", size, "Color", color,
         variant["sku"], 20, "", "", "", quantity]
        for index, (size, color, quantity, variant) in enumerate(zip(sizes, colors, counts, variants))
    ])

    enrich_spreadsheet_source_refs([product], workbook_bytes(rows), "inventory.xlsx", "inventory-file")

    quantity_refs = [
        ref for ref in product["source_refs"] if ref.get("field") == "inventory_quantity"
    ]
    assert [variant["inventory_quantity"] for variant in product["variants"]] == counts
    assert sorted(ref["cell"] for ref in quantity_refs) == [f"K{row}" for row in range(2, 10)]
    proposed, blockers, unapplied = merge_structured_product(None, product, {"title": product["title"]})
    assert blockers == [] and unapplied == []
    inventory_rows = [row for row in review_changes(None, product, proposed) if row["field"] == "inventory_quantity"]
    assert sorted(row["proposed"] for row in inventory_rows) == sorted(counts)
    assert sorted(row["source_ref"]["cell"] for row in inventory_rows) == [f"K{row}" for row in range(2, 10)]


def test_replaces_invalid_existing_sku_ref_on_anchored_sheet():
    existing = {"field": "variants[0].sku", "sheet": "Products", "cell": "Z9"}
    products = [{"title": "Lamp", "variants": [{"sku": "L-1", "price": "12"}], "source_refs": [
        {"field": "title", "sheet": "Products", "cell": "A2"}, existing,
    ]}]
    enrich_spreadsheet_source_refs(products, workbook_bytes([["Title", "SKU", "Price"], ["Lamp", "L-1", 12]]), "p.xlsx")
    assert existing not in products[0]["source_refs"]
    assert any(ref.get("field") == "sku" and ref.get("sheet") == "Products" and ref.get("cell") == "B2" for ref in products[0]["source_refs"])


def test_refuses_ambiguous_duplicate_product_rows():
    products = [{"title": "Lamp", "variants": [{"price": "12"}]}]
    enrich_spreadsheet_source_refs(products, workbook_bytes([["Title", "Price"], ["Lamp", 12], ["Lamp", 12]]), "p.xlsx")
    assert products[0]["source_refs"] == []


def test_unique_sku_selects_correct_row_when_title_is_duplicated():
    products = [{"title": "Lamp", "vendor": "Acme", "variants": [{"sku": "B", "price": 20}]}]
    enrich_spreadsheet_source_refs(products, workbook_bytes([
        ["Title", "Brand", "Product Code", "Unit Price"],
        ["Lamp", "Acme", "A", 10], ["Lamp", "Acme", "B", 20],
    ]), "p.xlsx")
    assert {(ref["field"], ref["cell"]) for ref in products[0]["source_refs"]} == {
        ("title", "A3"), ("vendor", "B3"), ("sku", "C3"), ("price", "D3")
    }


def test_keyboard_template_resolves_every_variant_continuation_cell_and_option_roles():
    workbook = (
        Path(__file__).resolve().parents[2]
        / "generated_shopify_samples/shopify_sample_products_keyboard.xlsx"
    ).read_bytes()
    products = [{
        "handle": "orbit-mechanical-keyboard",
        "title": "Orbit Mechanical Keyboard",
        "body_html": "<p>Compact 75% mechanical keyboard with hot-swappable switches.</p>",
        "vendor": "Orbit Works",
        "product_category": "Electronics > Computers > Computer Peripherals > Keyboards",
        "product_type": "Keyboards",
        "tags": ["mechanical", "usb-c", "desktop"],
        "published": True,
        "image_src": "https://cdn.example.com/orbit-mechanical-keyboard.jpg",
        "image_position": 1,
        "image_alt_text": "Orbit compact mechanical keyboard",
        "seo_title": "Orbit Mechanical Keyboard",
        "seo_description": "Orbit Mechanical Keyboard for Shopify extraction testing.",
        "google_shopping_mpn": "ORBIT75-TAC",
        "options": [
            {"name": "Switch", "values": ["Tactile", "Linear"]},
            {"name": "Layout", "values": ["US"]},
        ],
        "variants": [
            {
                "option1": "Tactile", "option2": "US", "sku": "ORBIT-KB-TAC-US",
                "grams": 890, "inventory_tracker": "shopify", "inventory_quantity": 13,
                "inventory_policy": "deny", "fulfillment_service": "manual", "price": 119,
                "requires_shipping": True, "taxable": True, "cost_per_item": 58,
                "variant_weight_unit": "g",
            },
            {
                "option1": "Linear", "option2": "US", "sku": "ORBIT-KB-LIN-US",
                "grams": 890, "inventory_tracker": "shopify", "inventory_quantity": 8,
                "inventory_policy": "deny", "fulfillment_service": "manual", "price": 119,
                "requires_shipping": True, "taxable": True, "cost_per_item": 58,
                "variant_weight_unit": "g",
            },
        ],
    }]

    enrich_spreadsheet_source_refs(products, workbook, "keyboard.xlsx", "keyboard-file")
    refs = products[0]["source_refs"]
    cells_by_path = {(ref["value_path"], ref.get("cell")) for ref in refs}
    linear = "variant[switch-linear-layout-us]"
    assert {
        (f"{linear}.sku", "O3"),
        (f"{linear}.price", "U3"),
        (f"{linear}.inventoryItem.cost", "AV3"),
        ("inventory[switch-linear-layout-us].inventory_quantity", "R3"),
        (f"{linear}.fulfillment_service", "T3"),
    } <= cells_by_path
    assert {
        ("option[switch].values", "I2", "label"),
        ("option[switch].values", "J2", "value"),
        ("option[switch].values", "I3", "label"),
        ("option[switch].values", "J3", "value"),
    } <= {(ref["value_path"], ref.get("cell"), ref.get("evidence_role")) for ref in refs}


def test_variants_range_emits_exact_refs_for_every_variant():
    products = [{
        "title": "Lamp", "variants": [{"sku": "L-1", "price": 12}, {"sku": "L-2", "price": 13}],
        "source_refs": [{"field": "variants", "sheet": "Spring Catalog", "cell_range": "C2:D3"}],
    }]
    data = multisheet_workbook_bytes([
        ("Archive", [["Title", "Price", "SKU"], ["Lamp", 99, "L-1"]]),
        ("Spring Catalog", [["Title", "Price", "SKU"], ["Lamp", 12, "L-1"], ["Lamp", 13, "L-2"]]),
    ])
    enrich_spreadsheet_source_refs(products, data, "p.xlsx")
    refs = [ref for ref in products[0]["source_refs"] if ref.get("field") in {"sku", "price"}]
    assert {(ref["value_path"], ref["cell"]) for ref in refs} == {
        ("variant[l-1].sku", "C2"), ("variant[l-1].price", "B2"),
        ("variant[l-2].sku", "C3"), ("variant[l-2].price", "B3"),
    }


def test_variant_refs_use_unique_sku_rows_and_reject_mismatched_price():
    products = [{
        "title": "Tee", "options": [{"name": "Size", "values": ["S", "M"]}],
        "variants": [
            {"option1": "S", "sku": "TEE-S", "price": 10},
            {"option1": "M", "sku": "TEE-M", "price": 999},
        ],
    }]
    enrich_spreadsheet_source_refs(products, workbook_bytes([
        ["Title", "SKU", "Price"], ["Tee", "TEE-S", 10], ["Tee", "TEE-M", 10],
    ]), "p.xlsx", source_file_id="source-file")
    refs = products[0]["source_refs"]
    assert next(ref for ref in refs if ref["value_path"] == "variant[size-s].price")["cell"] == "C2"
    assert not any(ref["value_path"] == "variant[size-m].price" for ref in refs)
    assert all(ref["source_file_id"] == "source-file" for ref in refs)


def test_identical_products_are_resolved_on_their_own_anchored_sheets():
    data = multisheet_workbook_bytes([
        ("Retail", [["Title", "Price", "SKU"], ["Lamp", 12, "L-1"]]),
        ("Wholesale", [["Title", "Price", "SKU"], ["Lamp", 12, "L-1"]]),
    ])
    products = [
        {"title": "Lamp", "variants": [{"sku": "L-1", "price": 12}], "source_refs": [{"field": "title", "sheet": name, "cell": "A2"}]}
        for name in ("Retail", "Wholesale")
    ]
    enrich_spreadsheet_source_refs(products, data, "p.xlsx")
    assert [next(ref for ref in product["source_refs"] if ref.get("field") == "sku")["sheet"] for product in products] == ["Retail", "Wholesale"]


def test_apostrophe_sheet_qualified_anchor_is_preserved():
    data = multisheet_workbook_bytes([("Maker's Picks", [["Title", "SKU"], ["Lamp", "L-1"]])])
    products = [{
        "title": "Lamp", "variants": [{"sku": "L-1"}],
        "source_refs": [{"field": "variants", "cell_range": "'Maker''s Picks'!A2:B2"}],
    }]
    enrich_spreadsheet_source_refs(products, data, "p.xlsx")
    sku = next(ref for ref in products[0]["source_refs"] if ref.get("field") == "sku")
    assert (sku["sheet"], sku["cell"]) == ("Maker's Picks", "B2")


def test_conflicting_sheet_anchors_remove_invalid_sku_and_do_not_guess():
    data = multisheet_workbook_bytes([
        ("One", [["Title", "SKU"], ["Lamp", "L-1"]]),
        ("Two", [["Title", "SKU"], ["Lamp", "L-1"]]),
    ])
    products = [{
        "title": "Lamp", "variants": [{"sku": "L-1"}],
        "source_refs": [
            {"field": "title", "sheet": "One", "cell": "A2"},
            {"field": "variants", "sheet": "Two", "cell_range": "A2:B2"},
            {"field": "sku", "sheet": "One", "cell": "A2"},
        ],
    }]
    enrich_spreadsheet_source_refs(products, data, "p.xlsx")
    assert not any(ref.get("field") == "sku" for ref in products[0]["source_refs"])


def test_unanchored_duplicate_across_sheets_is_unresolved():
    data = multisheet_workbook_bytes([
        ("One", [["Title", "SKU"], ["Lamp", "L-1"]]),
        ("Two", [["Title", "SKU"], ["Lamp", "L-1"]]),
    ])
    products = [{"title": "Lamp", "variants": [{"sku": "L-1"}]}]
    enrich_spreadsheet_source_refs(products, data, "p.xlsx")
    assert products[0]["source_refs"] == []


def test_wrong_sku_ref_is_not_repaired_from_another_sheet():
    data = multisheet_workbook_bytes([
        ("One", [["Title", "SKU"], ["Lamp", "WRONG"]]),
        ("Two", [["Title", "SKU"], ["Lamp", "L-1"]]),
    ])
    products = [{
        "title": "Lamp", "variants": [{"sku": "L-1"}],
        "source_refs": [
            {"field": "title", "sheet": "One", "cell": "A2"},
            {"field": "sku", "sheet": "One", "cell": "B2"},
        ],
    }]
    enrich_spreadsheet_source_refs(products, data, "p.xlsx")
    assert not any(ref.get("field") == "sku" for ref in products[0]["source_refs"])


def test_legacy_default_variant_cells_recover_canonical_sku_and_numeric_price_refs():
    products = [{
        "title": "Studio Mixer",
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [{
            "option1": "Default Title", "sku": "SMX-100", "price": "89.99",
        }],
        "source_refs": [
            {"field": "title", "value_path": "title", "sheet": "Products", "cell": "A2"},
            {"field": "sku", "value_path": "variant[Default Title].sku", "sheet": "Products", "cell": "B2"},
            {"field": "price", "value_path": "variant[Default Title].price", "sheet": "Products", "cell": "C2"},
        ],
    }]
    data = workbook_bytes([
        ["Title", "Code", "Amount"],
        ["Studio Mixer", "SMX-100", 89.99],
    ])

    enrich_spreadsheet_source_refs(products, data, "products.xlsx", "source-file")
    by_field = {ref["field"]: ref for ref in products[0]["source_refs"]}

    assert by_field["sku"]["value_path"] == "variant[title-default-title].sku"
    assert by_field["sku"]["cell"] == "B2"
    assert by_field["price"]["value_path"] == "variant[title-default-title].price"
    assert by_field["price"]["cell"] == "C2"
    assert by_field["price"]["source_file_id"] == "source-file"


def test_duplicate_or_wrong_legacy_spreadsheet_variant_claims_stay_unlinked():
    products = [{
        "title": "Studio Mixer",
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [{"option1": "Default Title", "sku": "SMX-100", "price": "89.99"}],
        "source_refs": [
            {"field": "title", "value_path": "title", "sheet": "Products", "cell": "A2"},
            {"field": "sku", "value_path": "variant[Default Title].sku", "sheet": "Products", "cell": "B2"},
            {"field": "sku", "value_path": "variant[Default Title].sku", "sheet": "Products", "cell": "B2"},
            {"field": "price", "value_path": "variant[Default Title].price", "sheet": "Products", "cell": "D2"},
        ],
    }]
    data = workbook_bytes([
        ["Title", "Code", "Amount", "Wrong"],
        ["Studio Mixer", "SMX-100", 89.99, 10],
    ])

    enrich_spreadsheet_source_refs(products, data, "products.xlsx")

    assert not any(ref["field"] in {"sku", "price"} for ref in products[0]["source_refs"])
    assert "sku" not in products[0]["variants"][0]
    assert "price" not in products[0]["variants"][0]


def test_spreadsheet_protected_values_require_verified_cells_even_with_model_origin():
    products = [{
        "title": "Lamp",
        "variants": [{"sku": "L-1", "price": 12, "cost_per_item": 5}],
        "value_origins": {
            "variant[l-1].sku": "ai_assisted",
            "variant[l-1].price": "defaulted",
            "variant[l-1].inventoryItem.cost": "derived",
        },
    }]
    data = workbook_bytes([
        ["Title", "SKU", "Price"],
        ["Lamp", "L-1", 12],
    ])

    enrich_spreadsheet_source_refs(products, data, "products.xlsx")

    variant = products[0]["variants"][0]
    assert variant["sku"] == "L-1"
    assert variant["price"] == 12
    assert "cost_per_item" not in variant
    assert products[0]["value_origins"]["variant[l-1].sku"] == "source"
    assert products[0]["value_origins"]["variant[l-1].price"] == "source"
