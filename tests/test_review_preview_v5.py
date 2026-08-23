from copy import deepcopy

import pytest

from api.agents.review_edit_sessions import (
    _apply_cell_operation,
    _blocking_field_errors,
    _canonical_review_value,
    _apply_ui_operation,
    _transform_structure,
)
from application.domain.shopify_mutation_plan import (
    build_review_plan_item,
    infer_positional_option_dimensions,
    merge_structured_product,
    normalize_review_plan_rows,
)


def _item(product, *, live=None):
    item = build_review_plan_item(
        item_id="item-1",
        currency_code="USD",
        source_index=3,
        source_product=deepcopy(product),
        live_snapshot=deepcopy(live),
        proposed=deepcopy(product),
        operation="update" if live else "create",
    )
    return normalize_review_plan_rows(
        {"review_contract_version": 5, "items": [item]}
    )["items"][0]


def test_v5_preview_authors_stable_cells_and_hides_default_title_scaffold():
    product = {
        "title": "Mug",
        "handle": "mug",
        "status": "DRAFT",
        "productOptions": [
            {"name": "Title", "values": [{"name": "Default Title"}]}
        ],
        "variants": [
            {
                "optionValues": [
                    {"optionName": "Title", "name": "Default Title"}
                ],
                "sku": "MUG-1",
                "price": 12,
            }
        ],
    }
    first = _item(product)
    second = _item(product)

    assert first["review_preview"]["option_dimensions"] == []
    assert [cell["cell_id"] for cell in first["review_preview"]["product"]["cells"]] == [
        cell["cell_id"] for cell in second["review_preview"]["product"]["cells"]
    ]
    variant = first["review_preview"]["variants"][0]
    assert variant["row_id"] == second["review_preview"]["variants"][0]["row_id"]
    assert {cell["column_key"] for cell in variant["cells"]} >= {"sku", "price"}
    assert not any(
        cell["column_key"].startswith("option")
        for cell in variant["cells"]
    ), "single-variant Shopify scaffolds must not leak option-value cells"


def test_v5_normalization_preserves_an_existing_preview_idempotently():
    product = {
        "title": "Stable shoe",
        "options": [{"name": "Size", "values": ["8", "10"]}],
        "variants": [
            {"option1": "8", "sku": "SHOE-8", "price": 80},
            {"option1": "10", "sku": "SHOE-10", "price": 90},
        ],
    }
    item = _item(product)
    first = normalize_review_plan_rows(
        {"review_contract_version": 5, "items": [item]}
    )
    second = normalize_review_plan_rows(first)

    assert second == first
    assert len(second["items"][0]["review_preview"]["variants"]) == 2


def test_structural_variant_blocker_only_blocks_affected_option_cells():
    product = {
        "title": "Blocked shoe",
        "vendor": "Northwind",
        "options": [{"name": "Size", "values": ["8", "10"]}],
        "variants": [
            {"title": "unknown", "sku": "SHOE-X", "price": 80},
            {"option1": "10", "sku": "SHOE-10", "price": 90},
        ],
    }
    proposed, blockers, unapplied = merge_structured_product(
        None, product, {"title": product["title"], "vendor": product["vendor"]}
    )
    item = build_review_plan_item(
        item_id="blocked-shoe",
        currency_code="USD",
        source_index=0,
        source_product=product,
        live_snapshot=None,
        proposed=proposed,
        operation="create",
        blockers=blockers,
        unapplied=unapplied,
    )
    item = normalize_review_plan_rows(
        {"review_contract_version": 5, "items": [item]}
    )["items"][0]

    product_cells = item["review_preview"]["product"]["cells"]
    first_variant = item["review_preview"]["variants"][0]["cells"]
    assert item["operation"] == "blocked"
    assert all(cell["disposition"] != "blocked" for cell in product_cells)
    assert next(cell for cell in first_variant if cell["column_key"] == "option1Value")["disposition"] == "blocked"
    assert next(cell for cell in first_variant if cell["column_key"] == "sku")["disposition"] == "applicable"
    assert next(cell for cell in first_variant if cell["column_key"] == "price")["disposition"] == "applicable"


def test_v5_preview_hydrates_unchanged_product_and_positional_variant_evidence():
    product = {
        "title": "Source Shirt",
        "vendor": "Northwind",
        "options": [{"name": "Color", "values": ["Black", "White"]}],
        "variants": [
            {"option1": "Black", "sku": "SHIRT-B", "price": "20"},
            {"option1": "White", "sku": "SHIRT-W", "price": "20"},
        ],
        "value_origins": {
            "title": "source",
            "vendor": "source",
            "variant[color-black].sku": "source",
            "variant[color-black].price": "source",
            "variant[color-white].sku": "source",
            "variant[color-white].price": "source",
        },
        "source_refs": [
            {"value_path": "title", "anchor_id": "title-anchor"},
            {"value_path": "vendor", "anchor_id": "vendor-anchor"},
            {"value_path": "variant[color-black].sku", "anchor_id": "black-sku"},
            {"value_path": "variant[color-black].price", "anchor_id": "black-price"},
            {"value_path": "variant[color-white].sku", "anchor_id": "white-sku"},
            {"value_path": "variant[color-white].price", "anchor_id": "white-price"},
        ],
    }
    item = _item(product, live=product)

    product_cells = {
        cell["column_key"]: cell
        for cell in item["review_preview"]["product"]["cells"]
    }
    assert product_cells["title"]["origin"] == "source"
    assert product_cells["title"]["source_ref"]["anchor_id"] == "title-anchor"
    assert product_cells["vendor"]["source_ref"]["anchor_id"] == "vendor-anchor"

    variants = item["review_preview"]["variants"]
    assert [
        next(cell for cell in row["cells"] if cell["column_key"] == "sku")["value_path"]
        for row in variants
    ] == ["variants[0].sku", "variants[1].sku"]
    assert [
        next(cell for cell in row["cells"] if cell["column_key"] == "price")["source_ref"]["anchor_id"]
        for row in variants
    ] == ["black-price", "white-price"]


def test_v5_cell_edit_removes_source_authority_and_revert_restores_it():
    product = {
        "title": "Source Mug",
        "variants": [{"sku": "MUG", "price": "12"}],
        "value_origins": {"title": "source"},
        "source_refs": [{"value_path": "title", "anchor_id": "title-anchor"}],
    }
    item = _item(product, live=product)
    working_plan = {"review_contract_version": 5, "items": [item]}
    title = next(
        cell
        for cell in item["review_preview"]["product"]["cells"]
        if cell["column_key"] == "title"
    )
    products = [{}, {}, {}, deepcopy(product)]

    products, history, cursor = _apply_cell_operation(
        products,
        working_plan,
        {
            "operation": "cell_edit",
            "cell_id": title["cell_id"],
            "value_path": title["value_path"],
            "value": "Merchant Mug",
        },
        [],
        0,
    )
    edited = next(
        cell
        for cell in working_plan["items"][0]["review_preview"]["product"]["cells"]
        if cell["column_key"] == "title"
    )
    assert edited["origin"] == "merchant_edited"
    assert "source_ref" not in edited

    products, _, _ = _apply_cell_operation(
        products,
        working_plan,
        {
            "operation": "cell_revert",
            "cell_id": edited["cell_id"],
            "value_path": edited["value_path"],
        },
        history,
        cursor,
    )
    reverted = next(
        cell
        for cell in working_plan["items"][0]["review_preview"]["product"]["cells"]
        if cell["column_key"] == "title"
    )
    assert products[3]["title"] == "Source Mug"
    assert reverted["origin"] == "source"
    assert reverted["source_ref"]["anchor_id"] == "title-anchor"


def test_v5_missing_variant_merchant_values_are_editable_required_cells():
    item = _item(
        {
            "title": "Shirt",
            "handle": "shirt",
            "status": "DRAFT",
            "variants": [{"sku": "", "price": None}],
        }
    )
    cells = {
        cell["column_key"]: cell
        for cell in item["review_preview"]["variants"][0]["cells"]
    }
    assert cells["sku"]["required"] and cells["sku"]["editable"]
    assert cells["price"]["required"] and cells["price"]["editable"]
    assert cells["sku"]["disposition"] == "blocked"
    assert cells["price"]["change_id"]


def test_structural_option_and_variant_rules_reject_stale_and_duplicate_state():
    product = {
        "title": "Shirt",
        "handle": "shirt",
        "productOptions": [{"name": "Size", "values": [{"name": "S"}]}],
        "variants": [
            {
                "optionValues": [{"optionName": "Size", "name": "S"}],
                "sku": "SHIRT-S",
                "price": 10,
            }
        ],
    }
    item = _item(product)
    with_color = _transform_structure(
        product,
        item,
        {"operation": "option_dimension_add", "name": "Color", "value": "Red"},
    )
    assert [option["name"] for option in with_color["productOptions"]] == [
        "Size",
        "Color",
    ]
    assert with_color["variants"][0]["optionValues"][-1]["name"] == "Red"

    refreshed = _item(with_color)
    with pytest.raises(ValueError, match="declared value"):
        _transform_structure(
            with_color,
            refreshed,
            {
                "operation": "variant_add",
                "option_values": {"Size": "S", "Color": "Blue"},
            },
        )
    with pytest.raises(ValueError, match="final variant"):
        _transform_structure(
            with_color,
            refreshed,
            {
                "operation": "variant_remove",
                "subject_identity": {
                    "kind": "variant_options",
                    "option_names": ["size", "color"],
                    "values": ["m", "red"],
                },
            },
        )


def test_cell_edit_creates_an_effective_change_for_an_unchanged_preview_cell():
    product = {
        "title": "Mug",
        "handle": "mug",
        "status": "DRAFT",
        "variants": [{"sku": "MUG", "price": 10}],
    }
    item = _item(product)
    working_plan = {"review_contract_version": 5, "items": [item]}
    vendor = next(
        cell
        for cell in item["review_preview"]["product"]["cells"]
        if cell["column_key"] == "vendor"
    )
    assert vendor["edit_target"] == "preview_cell"

    products, history, cursor = _apply_cell_operation(
        [{}, {}, {}, deepcopy(product)],
        working_plan,
        {
            "operation": "cell_edit",
            "cell_id": vendor["cell_id"],
            "value_path": vendor["value_path"],
            "value": "Northwind",
        },
        [],
        0,
    )

    assert products[3]["vendor"] == "Northwind"
    assert history[0]["operation"] == "cell_edit" and cursor == 1
    rebuilt = working_plan["items"][0]
    assert any(
        row["field"] == "vendor" and row["proposed"] == "Northwind"
        for row in rebuilt["review_changes"]
    )


def test_single_imported_variant_safely_matches_single_live_custom_variant():
    imported = {
        "title": "Aurora",
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [{"option1": "Default Title", "sku": "AUR-NEW", "price": 29}],
    }
    live = {
        "id": "gid://shopify/Product/1",
        "title": "Aurora",
        "options": [{"name": "Material", "values": ["Canvas"]}],
        "variants": [{
            "id": "gid://shopify/ProductVariant/1",
            "selectedOptions": [{"name": "Material", "value": "Canvas"}],
            "sku": "AUR-OLD",
            "price": "20",
        }],
    }

    proposed, blockers, _ = merge_structured_product(live, imported, {"title": "Aurora"})

    assert blockers == []
    assert len(proposed["variants"]) == 1
    assert proposed["variants"][0]["id"] == "gid://shopify/ProductVariant/1"
    assert proposed["variants"][0]["optionValues"] == [
        {"optionName": "Material", "name": "Canvas"}
    ]
    assert proposed["variants"][0]["sku"] == "AUR-NEW"
    assert proposed["variants"][0]["price"] == "29"
    assert proposed["_review_single_variant_match"]["matched"] is True


def test_ambiguous_multi_variant_schema_keeps_specific_supported_remedies():
    imported = {
        "title": "Heritage",
        "options": [{"name": "Size", "values": ["S", "M"]}],
        "variants": [
            {"option1": "S", "sku": "HER-S", "price": 20},
            {"option1": "M", "sku": "HER-M", "price": 20},
        ],
    }
    live = {
        "options": [{"name": "Material", "values": ["Canvas", "Leather"]}],
        "variants": [
            {"selectedOptions": [{"name": "Material", "value": "Canvas"}]},
            {"selectedOptions": [{"name": "Material", "value": "Leather"}]},
        ],
    }

    _, blockers, _ = merge_structured_product(live, imported, {"title": "Heritage"})
    mismatch = next(
        blocker for blocker in blockers if blocker["code"] == "INCOMPATIBLE_OPTION_SCHEMA"
    )
    assert mismatch["context"] == {
        "imported_option_names": ["Size"],
        "live_option_names": ["Material"],
    }
    assert mismatch["remedies"] == ["manage_options_variants", "exclude_product"]
    assert "Size" in mismatch["message"] and "Material" in mismatch["message"]


def test_blank_optional_inventory_is_no_operation_and_whole_numbers_are_valid():
    schema = {"editor": "integer", "required": False}
    assert _canonical_review_value({"edit_schema": schema}, "") is None
    assert _canonical_review_value({"edit_schema": schema}, None) is None
    assert _canonical_review_value({"edit_schema": schema}, "12") == 12
    with pytest.raises(ValueError, match="whole number"):
        _canonical_review_value({"edit_schema": schema}, "1.5")


def test_excluded_blocked_product_does_not_emit_blocking_field_errors():
    item = _item({"title": "Blocked", "variants": [{"sku": "", "price": 10}]})
    item["selection_intent"] = "exclude"
    assert _blocking_field_errors({"review_contract_version": 5, "items": [item]}) == []


def test_blocked_product_can_persist_an_explicit_exclusion():
    item = _item({"title": "Blocked", "variants": [{"sku": "", "price": 10}]})
    item.update({"operation": "blocked", "selectable": False, "selected": False, "selection_intent": "include"})
    plan = {"review_contract_version": 5, "items": [item]}

    _apply_ui_operation(
        plan,
        {},
        {
            "operation": "selection",
            "item_id": item["item_id"],
            "selected": False,
            "included_change_ids": [],
        },
    )

    assert item["selection_intent"] == "exclude"


def test_positional_size_and_color_are_derived_with_three_source_rows():
    product = {
        "title": "T-shirt",
        "variants": [
            {"option1": "S", "option2": "Red", "sku": "TEE-S", "price": 20},
            {"option1": "M", "option2": "Red", "sku": "TEE-M", "price": 20},
            {"option1": "L", "option2": "Red", "sku": "TEE-L", "price": 20},
        ],
    }
    assert infer_positional_option_dimensions(product)
    proposed, blockers, _ = merge_structured_product(None, product, {"title": "T-shirt"})
    assert blockers == []
    item = build_review_plan_item(
        item_id="tee", currency_code="USD", source_index=0,
        source_product=product, live_snapshot=None, proposed=proposed,
        operation="create",
    )
    item = normalize_review_plan_rows(
        {"review_contract_version": 5, "items": [item]}
    )["items"][0]

    assert [dimension["name"] for dimension in item["review_preview"]["option_dimensions"]] == ["Size", "Color"]
    assert all(
        dimension["origin"] == "derived" and not dimension["requires_name"]
        for dimension in item["review_preview"]["option_dimensions"]
    )
    assert [row["source_variant_index"] for row in item["review_preview"]["variants"]] == [0, 1, 2]
    assert [
        next(cell["value_path"] for cell in row["cells"] if cell["column_key"] == "sku")
        for row in item["review_preview"]["variants"]
    ] == ["variants[0].sku", "variants[1].sku", "variants[2].sku"]


def test_ambiguous_positional_dimension_stays_visible_until_merchant_names_it():
    product = {
        "title": "Poster",
        "variants": [
            {"option1": "Classic", "sku": "POSTER-C", "price": 12},
            {"option1": "Modern", "sku": "POSTER-M", "price": 12},
        ],
    }
    assert infer_positional_option_dimensions(product)
    proposed, blockers, _ = merge_structured_product(None, product, {"title": "Poster"})
    item = build_review_plan_item(
        item_id="poster", currency_code="USD", source_index=0,
        source_product=product, live_snapshot=None, proposed=proposed,
        operation="create", blockers=blockers,
    )
    item = normalize_review_plan_rows(
        {"review_contract_version": 5, "items": [item]}
    )["items"][0]
    dimension = item["review_preview"]["option_dimensions"][0]
    assert dimension["name"] == "" and dimension["requires_name"]
    assert len(item["review_preview"]["variants"]) == 2

    renamed = _transform_structure(
        product,
        item,
        {
            "operation": "option_dimension_rename",
            "dimension_id": dimension["dimension_id"],
            "name": "Style",
        },
    )
    assert renamed["productOptions"][0]["name"] == "Style"
    assert renamed["_review_option_dimensions"][0]["origin"] == "merchant_edited"
