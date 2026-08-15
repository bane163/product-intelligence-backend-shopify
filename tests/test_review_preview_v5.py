from copy import deepcopy

import pytest

from api.agents.review_edit_sessions import _apply_cell_operation, _transform_structure
from application.domain.shopify_mutation_plan import (
    build_review_plan_item,
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
