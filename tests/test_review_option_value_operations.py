from __future__ import annotations

from copy import deepcopy

import pytest
from httpx import ASGITransport, AsyncClient

from app_context import get_app_context
from application.domain.shopify_mutation_plan import (
    OptionValueEditError,
    apply_option_value_operation,
    build_review_plan_item,
    normalize_review_plan_rows,
)
from main import app


def _option_fixture() -> tuple[
    dict[str, object], dict[str, object], dict[str, object]
]:
    live = {
        "id": "gid://shopify/Product/1",
        "title": "Shirt",
        "handle": "shirt",
        "vendor": "Live vendor",
        "options": [
            {"id": "option-color", "name": "Color", "optionValues": [{"id": "red-id", "name": "Red"}]},
            {"id": "option-size", "name": "Size", "optionValues": [{"id": "s-id", "name": "S"}, {"id": "m-id", "name": "M"}]},
        ],
        "variants": {
            "nodes": [
                {
                    "id": "live-red-s",
                    "selectedOptions": [{"name": "Color", "value": "Red"}, {"name": "Size", "value": "S"}],
                    "price": "10.00",
                    "sku": "LIVE-RS",
                },
                {
                    "id": "live-red-m",
                    "selectedOptions": [{"name": "Color", "value": "Red"}, {"name": "Size", "value": "M"}],
                    "price": "12.00",
                    "sku": "LIVE-RM",
                },
            ]
        },
    }
    source = {
        "title": "Shirt",
        "handle": "shirt",
        "vendor": "Imported vendor",
        "options": [
            {"name": "Color", "values": ["Red", "Blue"]},
            {"name": "Size", "values": ["S", "M", "L"]},
        ],
        "variants": [
            {
                "id": "forged-product-variant-id",
                "product_id": "forged-product-id",
                "option1": "Red",
                "option2": "S",
                "selected_options": [{"name": "Color", "value": "Red"}, {"name": "Size", "value": "S"}],
                "price": 10,
                "compare_at_price": 15,
                "taxable": True,
                "inventory_policy": "deny",
                "requires_shipping": True,
                "tracked": True,
                "weight": 2,
                "weight_unit": "kg",
                "cost_per_item": 4,
                "sku": "RED-S",
                "barcode": "111",
                "inventory_quantity": 8,
                "inventory_item": {"id": "inventory-red-s", "cost": 4, "inventoryQuantity": 8},
            },
            {
                "option1": "Blue",
                "option2": "S",
                "selected_options": [{"name": "Color", "value": "Blue"}, {"name": "Size", "value": "S"}],
                "price": 20,
                "compare_at_price": 25,
                "taxable": False,
                "variant_tax_code": "PC000000",
                "inventory_policy": "continue",
                "inventory_tracker": "shopify",
                "requires_shipping": False,
                "fulfillment_service": "manual",
                "tracked": False,
                "weight": 3,
                "variant_weight_unit": "lb",
                "cost_per_item": 7,
                "sku": "BLUE-S",
                "barcode": "222",
                "inventory_quantity": 5,
            },
            {
                "option1": "Blue",
                "option2": "M",
                "selected_options": [{"name": "Color", "value": "Blue"}, {"name": "Size", "value": "M"}],
                "price": 30,
                "compare_at_price": 35,
                "taxable": True,
                "inventory_policy": "deny",
                "requires_shipping": True,
                "tracked": True,
                "weight": 4,
                "weight_unit": "kg",
                "cost_per_item": 9,
                "sku": "BLUE-M",
                "barcode": "333",
                "inventory_quantity": 6,
            },
        ],
    }
    proposed = {
        "id": "gid://shopify/Product/1",
        "title": "Shirt",
        "handle": "shirt",
        "vendor": "Imported vendor",
        "productOptions": [
            {"name": "Color", "position": 1, "values": [{"name": "Red"}, {"name": "Blue"}]},
            {"name": "Size", "position": 2, "values": [{"name": "S"}, {"name": "M"}, {"name": "L"}]},
        ],
        "variants": [
            {
                "id": "live-red-s",
                "optionValues": [{"optionName": "Color", "name": "Red"}, {"optionName": "Size", "name": "S"}],
                "price": "10.00",
                "sku": "RED-S",
            },
            {
                "optionValues": [{"optionName": "Color", "name": "Blue"}, {"optionName": "Size", "name": "S"}],
                "price": "20.00",
                "compareAtPrice": "25.00",
                "taxable": False,
                "inventoryPolicy": "CONTINUE",
                "inventoryItem": {"cost": "7", "tracked": False, "requiresShipping": False},
                "sku": "BLUE-S",
                "barcode": "222",
            },
            {
                "optionValues": [{"optionName": "Color", "name": "Blue"}, {"optionName": "Size", "name": "M"}],
                "price": "30.00",
                "compareAtPrice": "35.00",
                "taxable": True,
                "inventoryPolicy": "DENY",
                "inventoryItem": {"cost": "9", "tracked": True, "requiresShipping": True},
                "sku": "BLUE-M",
                "barcode": "333",
            },
        ],
    }
    return live, source, proposed


def _build_item() -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    live, source, proposed = _option_fixture()
    item = build_review_plan_item(
        item_id="option-item",
        currency_code="CAD",
        source_index=0,
        source_product=source,
        live_snapshot=live,
        proposed=proposed,
        operation="update",
    )
    return live, source, proposed, item


def _option_row(item: dict[str, object]) -> dict[str, object]:
    return next(
        row
        for row in item["review_changes"]
        if row["scope"] == "option" and row["subject"] == "Color"
    )


def test_rename_preserves_ids_and_updates_every_variant_reference_shape():
    product = {
        "options": [{"id": "option-id", "name": "Color", "values": [{"id": "red-id", "name": "Red"}, {"id": "blue-id", "name": "Blue"}]}],
        "variants": [
            {
                "id": "variant-id",
                "option1": "Blue",
                "selectedOptions": [{"id": "selected-id", "name": "Color", "value": "Blue"}],
                "optionValues": [{"id": "value-id", "optionName": "Color", "name": "Blue"}],
                "sku": "KEEP",
            }
        ],
    }

    renamed = apply_option_value_operation(
        product,
        option_name="color",
        action="rename",
        from_value="blue",
        to_value="Azure",
    )

    assert renamed["options"][0]["id"] == "option-id"
    assert renamed["options"][0]["values"][1] == {"id": "blue-id", "name": "Azure"}
    variant = renamed["variants"][0]
    assert variant["id"] == "variant-id"
    assert variant["sku"] == "KEEP"
    assert variant["option1"] == "Azure"
    assert variant["selectedOptions"][0] == {"id": "selected-id", "name": "Color", "value": "Azure"}
    assert variant["optionValues"][0] == {"id": "value-id", "optionName": "Color", "name": "Azure"}


def test_reorder_changes_only_value_and_canonical_variant_order():
    product = {
        "options": [{"name": "Color", "values": ["Red", "Blue"]}],
        "variants": [
            {"id": "red-id", "option1": "Red", "sku": "RED"},
            {"id": "blue-id", "option1": "Blue", "sku": "BLUE"},
        ],
    }

    reordered = apply_option_value_operation(
        product,
        option_name="Color",
        action="reorder",
        values=["Blue", "Red"],
    )

    assert reordered["options"][0]["values"] == ["Blue", "Red"]
    assert [(variant["id"], variant["sku"]) for variant in reordered["variants"]] == [
        ("blue-id", "BLUE"),
        ("red-id", "RED"),
    ]


def test_sparse_add_clones_each_represented_other_tuple_and_clears_unsafe_fields():
    _live, source, _proposed = _option_fixture()

    added = apply_option_value_operation(
        source, option_name="Color", action="add", value="Green"
    )

    greens = [variant for variant in added["variants"] if variant["option1"] == "Green"]
    assert [variant["option2"] for variant in greens] == ["S", "M"]
    assert [variant["price"] for variant in greens] == [20, 30]
    assert [variant["compare_at_price"] for variant in greens] == [25, 35]
    assert [variant["cost_per_item"] for variant in greens] == [7, 9]
    assert greens[0]["variant_tax_code"] == "PC000000"
    assert greens[0]["inventory_policy"] == "continue"
    assert greens[0]["inventory_tracker"] == "shopify"
    assert greens[0]["requires_shipping"] is False
    assert greens[0]["fulfillment_service"] == "manual"
    assert greens[0]["tracked"] is False
    assert greens[0]["weight"] == 3
    assert greens[0]["variant_weight_unit"] == "lb"
    assert all(variant["sku"] == "" and variant["barcode"] == "" for variant in greens)
    assert all("id" not in variant and "product_id" not in variant for variant in greens)
    assert all("inventory_quantity" not in variant for variant in greens)
    assert all(
        not isinstance(variant.get("inventory_item"), dict)
        or "id" not in variant["inventory_item"]
        for variant in greens
    )


def test_add_clone_preserves_camel_case_and_nested_commerce_aliases():
    product = {
        "productOptions": [
            {"name": "Color", "values": [{"name": "Red"}]}
        ],
        "variants": [{
            "optionValues": [{"optionName": "Color", "name": "Red"}],
            "inventoryPolicy": "CONTINUE",
            "taxable": False,
            "taxCode": "PC040100",
            "requiresShipping": False,
            "tracked": True,
            "weight": 1.5,
            "weightUnit": "KILOGRAMS",
            "inventoryItem": {
                "cost": "8.25",
                "tracked": True,
                "requiresShipping": False,
                "measurement": {
                    "weight": {"value": 1.5, "unit": "KILOGRAMS"}
                },
                "id": "unsafe-inventory-id",
            },
            "inventoryQuantity": 17,
        }],
    }

    added = apply_option_value_operation(
        product, option_name="Color", action="add", value="Green"
    )

    green = next(
        variant
        for variant in added["variants"]
        if variant["optionValues"][0]["name"] == "Green"
    )
    assert green["inventoryPolicy"] == "CONTINUE"
    assert green["taxable"] is False
    assert green["taxCode"] == "PC040100"
    assert green["requiresShipping"] is False
    assert green["tracked"] is True
    assert green["weight"] == 1.5
    assert green["weightUnit"] == "KILOGRAMS"
    assert green["inventoryItem"] == {
        "cost": "8.25",
        "tracked": True,
        "requiresShipping": False,
        "measurement": {
            "weight": {"value": 1.5, "unit": "KILOGRAMS"}
        },
    }
    assert "inventoryQuantity" not in green


def test_remove_deletes_only_variants_that_reference_the_removed_value():
    _live, source, _proposed = _option_fixture()

    removed = apply_option_value_operation(
        source, option_name="Color", action="remove", value="Blue"
    )

    assert removed["options"][0]["values"] == ["Red"]
    assert [(variant["option1"], variant["option2"]) for variant in removed["variants"]] == [
        ("Red", "S")
    ]
    assert removed["variants"][0]["sku"] == "RED-S"


def test_item_builder_exposes_exact_live_and_proposed_only_impacts():
    _live, _source, _proposed, item = _build_item()
    row = _option_row(item)

    assert row["edit_schema"]["editor"] == "option_values"
    assert row["option_value_impacts"] == {
        "Red": {"total": 1, "live": 1, "proposed_only": 0},
        "Blue": {"total": 2, "live": 0, "proposed_only": 2},
    }


@pytest.mark.parametrize(
    ("product", "kwargs", "code"),
    [
        (
            {"options": [{"name": "Color", "values": ["Red"]}], "variants": [{"option1": "Red"}]},
            {"option_name": "Color", "action": "add", "value": " "},
            "BLANK_OPTION_VALUE",
        ),
        (
            {"options": [{"name": "Color", "values": ["Red", " "]}], "variants": []},
            {"option_name": "Color", "action": "add", "value": "Green"},
            "BLANK_OPTION_VALUE",
        ),
        (
            {"options": [{"name": "Color", "values": ["Red", " red "]}], "variants": []},
            {"option_name": "Color", "action": "add", "value": "Green"},
            "DUPLICATE_OPTION_VALUE",
        ),
        (
            {
                "options": [{"name": "Color", "values": ["Red"]}],
                "variants": [{"option1": "Purple"}],
            },
            {"option_name": "Color", "action": "add", "value": "Green"},
            "UNDECLARED_OPTION_VALUE",
        ),
        (
            {"options": [{"name": "Color", "values": ["Red"]}], "variants": [{"option1": "Red"}]},
            {"option_name": "Color", "action": "add", "value": "red"},
            "DUPLICATE_OPTION_VALUE",
        ),
        (
            {"options": [{"name": "Color", "values": ["Red"]}], "variants": [{"option1": "Red"}]},
            {"option_name": "Color", "action": "remove", "value": "Red"},
            "FINAL_OPTION_VALUE",
        ),
        (
            {"options": [{"name": "Color", "values": ["Red", "Blue"]}], "variants": [{"option1": "Red"}, {"option1": "Blue"}]},
            {"option_name": "Color", "action": "reorder", "values": ["Red"]},
            "INVALID_OPTION_REORDER",
        ),
        (
            {"options": [{"name": "Color", "values": ["Red"]}, {"name": "color", "values": ["Blue"]}], "variants": []},
            {"option_name": "Color", "action": "add", "value": "Green"},
            "AMBIGUOUS_OPTION_TARGET",
        ),
    ],
)
def test_domain_option_validation_is_structured(product, kwargs, code):
    with pytest.raises(OptionValueEditError) as exc_info:
        apply_option_value_operation(product, **kwargs)
    assert exc_info.value.code == code


def test_add_rejects_projected_2049th_variant():
    values = [str(index) for index in range(2048)]
    product = {
        "options": [{"name": "Number", "values": values}],
        "variants": [{"option1": value} for value in values],
    }

    with pytest.raises(OptionValueEditError) as exc_info:
        apply_option_value_operation(
            product, option_name="Number", action="add", value="2048"
        )

    assert exc_info.value.code == "VARIANT_LIMIT_EXCEEDED"


def test_named_and_positional_variant_representations_must_agree():
    product = {
        "options": [{"name": "Color", "values": ["Red", "Blue"]}],
        "variants": [{
            "option1": "Red",
            "selectedOptions": [{"name": "Color", "value": "Blue"}],
        }],
    }

    with pytest.raises(OptionValueEditError) as exc_info:
        apply_option_value_operation(
            product, option_name="Color", action="add", value="Green"
        )

    assert exc_info.value.code == "INCONSISTENT_VARIANT_OPTIONS"


def test_stored_row_identity_hydration_fails_safe_on_ambiguous_subject():
    ambiguous_subject = "Color: Blue / Size: S / Size: M"
    plan = normalize_review_plan_rows({
        "review_contract_version": 2,
        "items": [{
            "item_id": "ambiguous-item",
            "proposed": {
                "productOptions": [
                    {
                        "name": "Color",
                        "values": [
                            {"name": "Blue / Size: S"},
                            {"name": "Blue"},
                        ],
                    },
                    {
                        "name": "Size",
                        "values": [
                            {"name": "M"},
                            {"name": "S / Size: M"},
                        ],
                    },
                ],
                "variants": [
                    {
                        "optionValues": [
                            {
                                "optionName": "Color",
                                "name": "Blue / Size: S",
                            },
                            {"optionName": "Size", "name": "M"},
                        ],
                        "price": "10",
                    },
                    {
                        "optionValues": [
                            {"optionName": "Color", "name": "Blue"},
                            {
                                "optionName": "Size",
                                "name": "S / Size: M",
                            },
                        ],
                        "price": "20",
                    },
                ],
            },
            "review_changes": [{
                "change_id": "ambiguous-price",
                "value_path": "variant[ambiguous].price",
                "scope": "variant",
                "subject": ambiguous_subject,
                "field": "price",
                "current": None,
                "proposed": "10",
                "disposition": "applicable",
            }],
        }],
    })

    row = plan["items"][0]["review_changes"][0]
    assert "subject_identity" not in row
    assert row["editable"] is False
    identified = deepcopy(plan)
    identified["items"][0]["review_changes"][0]["subject_identity"] = {
        "kind": "variant_options",
        "option_names": ["color", "size"],
        "values": ["blue / size: s", "m"],
    }
    identified = normalize_review_plan_rows(identified)
    assert identified["items"][0]["review_changes"][0]["editable"] is True


def test_stored_option_row_hydrates_dedicated_editor_and_authoritative_impacts():
    plan = normalize_review_plan_rows({
        "review_contract_version": 2,
        "items": [{
            "item_id": "legacy-option-item",
            "live_snapshot": {
                "options": [{
                    "name": "Color",
                    "optionValues": [{"name": "Red"}, {"name": "Blue"}],
                }],
                "variants": {"nodes": [
                    {
                        "id": "live-red",
                        "selectedOptions": [{"name": "Color", "value": "Red"}],
                    },
                    {
                        "id": "live-blue",
                        "selectedOptions": [{"name": "Color", "value": "Blue"}],
                    },
                ]},
            },
            "proposed": {
                "productOptions": [{
                    "name": "Color",
                    "values": [
                        {"name": "Red"},
                        {"name": "Blue"},
                        {"name": "Green"},
                    ],
                }],
                "variants": [
                    {
                        "id": "live-red",
                        "optionValues": [{"optionName": "Color", "name": "Red"}],
                    },
                    {
                        "optionValues": [{"optionName": "Color", "name": "Blue"}],
                    },
                    {
                        "optionValues": [{"optionName": "Color", "name": "Green"}],
                    },
                ],
            },
            "review_changes": [{
                "change_id": "legacy-color-values",
                "value_path": "option[color].values",
                "scope": "option",
                "subject": "Color",
                "field": "values",
                "current": ["Red", "Blue"],
                "proposed": ["Red", "Blue", "Green"],
                "disposition": "applicable",
                "edit_schema": {
                    "editor": "list",
                    "required": True,
                    "same_count_only": True,
                },
            }],
        }],
    })

    row = plan["items"][0]["review_changes"][0]
    assert row["edit_schema"] == {
        "editor": "option_values",
        "required": True,
        "item_type": "text",
    }
    assert row["option_value_impacts"] == {
        "Red": {"total": 1, "live": 1, "proposed_only": 0},
        "Blue": {"total": 1, "live": 1, "proposed_only": 0},
        "Green": {"total": 1, "live": 0, "proposed_only": 1},
    }
    assert row["presentation"] == "merchant"
    assert row["editable"] is True


@pytest.mark.asyncio
async def test_ambiguous_variant_row_edit_is_rejected_atomically():
    service = get_app_context().services.supabase._service
    shop = "ambiguous-target.myshopify.com"
    draft_id = "ambiguous-target-draft"
    ambiguous_subject = "Color: Blue / Size: S / Size: M"
    source = {
        "title": "Ambiguous product",
        "options": [
            {
                "name": "Color",
                "values": ["Blue / Size: S", "Blue"],
            },
            {
                "name": "Size",
                "values": ["M", "S / Size: M"],
            },
        ],
        "variants": [
            {
                "option1": "Blue / Size: S",
                "option2": "M",
                "price": 10,
            },
            {
                "option1": "Blue",
                "option2": "S / Size: M",
                "price": 20,
            },
        ],
    }
    proposed = {
        "title": "Ambiguous product",
        "productOptions": [
            {
                "name": "Color",
                "values": [
                    {"name": "Blue / Size: S"},
                    {"name": "Blue"},
                ],
            },
            {
                "name": "Size",
                "values": [
                    {"name": "M"},
                    {"name": "S / Size: M"},
                ],
            },
        ],
        "variants": [
            {
                "optionValues": [
                    {
                        "optionName": "Color",
                        "name": "Blue / Size: S",
                    },
                    {"optionName": "Size", "name": "M"},
                ],
                "price": "10",
            },
            {
                "optionValues": [
                    {"optionName": "Color", "name": "Blue"},
                    {
                        "optionName": "Size",
                        "name": "S / Size: M",
                    },
                ],
                "price": "20",
            },
        ],
    }
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 1,
        "products": [source],
    }
    service.mutation_plans["ambiguous-target-plan"] = {
        "plan_id": "ambiguous-target-plan",
        "draft_id": draft_id,
        "shop_domain": shop,
        "version": 1,
        "status": "review",
        "review_contract_version": 2,
        "items": [{
            "item_id": "ambiguous-target-item",
            "source_index": 0,
            "proposed": proposed,
            "review_changes": [{
                "change_id": "ambiguous-price",
                "value_path": "variant[ambiguous].price",
                "scope": "variant",
                "subject": ambiguous_subject,
                "field": "price",
                "current": None,
                "proposed": "10",
                "disposition": "applicable",
                "included": True,
            }],
        }],
    }
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            base, headers=headers, json={"surface": "grid", "base_revision": 1}
        )
        session_id = created.json()["session"]["session_id"]
        row = created.json()["session"]["working_plan"]["items"][0][
            "review_changes"
        ][0]
        pending_before = deepcopy(created.json()["session"]["pending_products"])
        rejected = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 1, "operation": {
                "type": "value_edit",
                "change_id": row["change_id"],
                "value_path": row["value_path"],
                "value": "99",
            }},
        )
        active = await client.get(base, headers=headers)

    assert row["editable"] is False
    assert rejected.status_code == 422
    detail = rejected.json()["detail"]
    assert detail["code"] == "REVIEW_FIELD_VALIDATION_FAILED"
    assert detail["field_errors"][0]["code"] == "REVIEW_FIELD_READ_ONLY"
    assert active.json()["session"]["edit_revision"] == 1
    assert active.json()["session"]["pending_products"] == pending_before
    assert active.json()["session"]["history"] == []


@pytest.mark.asyncio
async def test_representation_mismatch_is_rejected_atomically_by_endpoint():
    service, shop, draft_id, item = await _seed_session("representation-mismatch")
    service.product_drafts[draft_id]["products"][0]["variants"][0][
        "selected_options"
    ][0]["value"] = "Blue"
    row = _option_row(item)
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            base,
            headers=headers,
            json={"surface": "grid", "base_revision": 1},
        )
        session_id = created.json()["session"]["session_id"]
        pending_before = deepcopy(created.json()["session"]["pending_products"])
        rejected = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={
                "expected_edit_revision": 1,
                "operation": {
                    "type": "option_values_edit",
                    "change_id": row["change_id"],
                    "value_path": row["value_path"],
                    "action": "add",
                    "value": "Green",
                },
            },
        )
        active = await client.get(base, headers=headers)

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["field_errors"][0]["code"] == (
        "INCONSISTENT_VARIANT_OPTIONS"
    )
    assert active.json()["session"]["edit_revision"] == 1
    assert active.json()["session"]["pending_products"] == pending_before
    assert active.json()["session"]["history"] == []


@pytest.mark.asyncio
async def test_undeclared_variant_value_is_rejected_atomically_by_endpoint():
    service, shop, draft_id, item = await _seed_session("undeclared-membership")
    variant = service.product_drafts[draft_id]["products"][0]["variants"][0]
    variant["option1"] = "Purple"
    variant["selected_options"][0]["value"] = "Purple"
    row = _option_row(item)
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            base, headers=headers, json={"surface": "grid", "base_revision": 1}
        )
        session_id = created.json()["session"]["session_id"]
        pending_before = deepcopy(created.json()["session"]["pending_products"])
        rejected = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 1, "operation": {
                "type": "option_values_edit",
                "change_id": row["change_id"],
                "value_path": row["value_path"],
                "action": "add",
                "value": "Green",
            }},
        )
        active = await client.get(base, headers=headers)

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["field_errors"][0]["code"] == (
        "UNDECLARED_OPTION_VALUE"
    )
    assert active.json()["session"]["edit_revision"] == 1
    assert active.json()["session"]["pending_products"] == pending_before
    assert active.json()["session"]["history"] == []


async def _seed_session(
    suffix: str,
) -> tuple[object, str, str, dict[str, object]]:
    service = get_app_context().services.supabase._service
    live, source, _proposed, item = _build_item()
    shop = f"option-{suffix}.myshopify.com"
    draft_id = f"option-{suffix}-draft"
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 1,
        "products": [deepcopy(source)],
    }
    service.mutation_plans[f"option-{suffix}-plan"] = {
        "plan_id": f"option-{suffix}-plan",
        "draft_id": draft_id,
        "shop_domain": shop,
        "version": 1,
        "status": "review",
        "review_contract_version": 2,
        "items": [item],
    }
    return service, shop, draft_id, item


@pytest.mark.asyncio
async def test_api_add_rebuilds_one_item_without_shopify_and_round_trips_history(monkeypatch):
    import api.agents.review_edit_sessions as sessions_api

    service, shop, draft_id, item = await _seed_session("history")

    async def unexpected_shopify(*args, **kwargs):
        raise AssertionError("option edit rebuild must not call Shopify")

    monkeypatch.setattr(sessions_api, "_offline_client", unexpected_shopify, raising=False)
    row = _option_row(item)
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(base, headers=headers, json={"surface": "grid", "base_revision": 1})
        session_id = created.json()["session"]["session_id"]
        added = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 1, "operation": {
                "type": "option_values_edit",
                "change_id": row["change_id"],
                "value_path": row["value_path"],
                "action": "add",
                "value": "Green",
            }},
        )
        added_session = added.json()["session"]
        added_row = _option_row(added_session["working_plan"]["items"][0])
        reverted = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 2, "operation": {
                "type": "value_revert",
                "change_id": added_row["change_id"],
                "value_path": added_row["value_path"],
            }},
        )
        undone = await client.post(
            f"{base}/{session_id}/undo",
            headers=headers,
            json={"expected_edit_revision": 3},
        )
        redone = await client.post(
            f"{base}/{session_id}/redo",
            headers=headers,
            json={"expected_edit_revision": 4},
        )
        reset = await client.post(
            f"{base}/{session_id}/reset",
            headers=headers,
            json={"expected_edit_revision": 5},
        )

    assert added.status_code == 200, added.text
    assert added_session["working_plan"]["items"][0]["item_id"] == "option-item"
    assert added_row["option_value_impacts"]["Green"] == {
        "total": 2,
        "live": 0,
        "proposed_only": 2,
    }
    green_rows = [
        row
        for row in added_session["working_plan"]["items"][0]["review_changes"]
        if "Color: Green" in str(row.get("subject"))
        and row.get("field") in {"sku", "barcode"}
    ]
    assert green_rows
    assert all(
        row["proposed"] == ""
        and row["presentation"] == "merchant"
        and row["editable"] is True
        for row in green_rows
    )
    assert [variant["option2"] for variant in added_session["pending_products"][0]["variants"] if variant["option1"] == "Green"] == ["S", "M"]
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["session"]["pending_products"][0]["options"][0]["values"] == ["Red", "Blue"]
    assert len(undone.json()["session"]["pending_products"][0]["variants"]) == 5
    assert len(redone.json()["session"]["pending_products"][0]["variants"]) == 3
    assert reset.json()["session"]["pending_products"][0] == service.product_drafts[draft_id]["products"][0]
    assert reset.json()["session"]["history"] == []


@pytest.mark.asyncio
async def test_structural_edits_preserve_scalar_edits_and_selection():
    _service, shop, draft_id, item = await _seed_session("scalar")
    option_row = _option_row(item)
    vendor_row = next(row for row in item["review_changes"] if row["field"] == "vendor")
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(base, headers=headers, json={"surface": "grid", "base_revision": 1})
        session_id = created.json()["session"]["session_id"]
        scalar = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 1, "operation": {
                "type": "value_edit",
                "change_id": vendor_row["change_id"],
                "value_path": vendor_row["value_path"],
                "value": "Merchant vendor",
            }},
        )
        structural = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 2, "operation": {
                "type": "option_values_edit",
                "change_id": option_row["change_id"],
                "value_path": option_row["value_path"],
                "action": "add",
                "value": "Green",
            }},
        )

    assert scalar.status_code == 200, scalar.text
    assert structural.status_code == 200, structural.text
    session = structural.json()["session"]
    rebuilt = session["working_plan"]["items"][0]
    assert session["pending_products"][0]["vendor"] == "Merchant vendor"
    assert rebuilt["proposed"]["vendor"] == "Merchant vendor"
    assert next(row for row in rebuilt["review_changes"] if row["field"] == "vendor")["proposed"] == "Merchant vendor"
    assert rebuilt["selected"] is True


@pytest.mark.asyncio
async def test_option_revert_preserves_later_scalar_selection_and_unrelated_option_edit():
    _service, shop, draft_id, item = await _seed_session("selective-revert")
    color_row = _option_row(item)
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(base, headers=headers, json={"surface": "grid", "base_revision": 1})
        session_id = created.json()["session"]["session_id"]
        added = await client.patch(
            f"{base}/{session_id}", headers=headers,
            json={"expected_edit_revision": 1, "operation": {
                "type": "option_values_edit", "change_id": color_row["change_id"],
                "value_path": color_row["value_path"], "action": "add", "value": "Green",
            }},
        )
        added_item = added.json()["session"]["working_plan"]["items"][0]
        vendor_row = next(row for row in added_item["review_changes"] if row["field"] == "vendor")
        size_row = next(
            row for row in added_item["review_changes"]
            if row["scope"] == "option" and row["subject"] == "Size"
        )
        scalar = await client.patch(
            f"{base}/{session_id}", headers=headers,
            json={"expected_edit_revision": 2, "operation": {
                "type": "value_edit", "change_id": vendor_row["change_id"],
                "value_path": vendor_row["value_path"], "value": "Later scalar",
            }},
        )
        unrelated = await client.patch(
            f"{base}/{session_id}", headers=headers,
            json={"expected_edit_revision": 3, "operation": {
                "type": "option_values_edit", "change_id": size_row["change_id"],
                "value_path": size_row["value_path"], "action": "reorder",
                "values": ["L", "M", "S"],
            }},
        )
        current_item = unrelated.json()["session"]["working_plan"]["items"][0]
        required_ids = [
            row["change_id"] for row in current_item["review_changes"]
            if row.get("required") and row.get("disposition") == "applicable"
        ]
        selected = await client.patch(
            f"{base}/{session_id}", headers=headers,
            json={"expected_edit_revision": 4, "operation": {
                "type": "selection", "item_id": current_item["item_id"],
                "selected": True, "included_change_ids": required_ids,
            }},
        )
        selected_item = selected.json()["session"]["working_plan"]["items"][0]
        current_color = next(
            row for row in selected_item["review_changes"]
            if row["scope"] == "option" and row["subject"] == "Color"
        )
        reverted = await client.patch(
            f"{base}/{session_id}", headers=headers,
            json={"expected_edit_revision": 5, "operation": {
                "type": "value_revert", "change_id": current_color["change_id"],
                "value_path": current_color["value_path"],
            }},
        )

    assert scalar.status_code == 200 and unrelated.status_code == 200
    assert selected.status_code == 200 and reverted.status_code == 200, reverted.text
    session = reverted.json()["session"]
    product = session["pending_products"][0]
    assert product["vendor"] == "Later scalar"
    assert product["options"][0]["values"] == ["Red", "Blue"]
    assert product["options"][1]["values"] == ["L", "M", "S"]
    assert all(variant["option1"] != "Green" for variant in product["variants"])
    reverted_item = session["working_plan"]["items"][0]
    assert reverted_item["selected"] is True
    assert {
        row["change_id"] for row in reverted_item["review_changes"] if row.get("included")
    } == set(required_ids)


@pytest.mark.asyncio
async def test_chained_option_reverts_do_not_resurrect_another_option_edit():
    _service, shop, draft_id, item = await _seed_session("chained-reverts")
    color_row = _option_row(item)
    size_row = next(
        row
        for row in item["review_changes"]
        if row["scope"] == "option" and row["subject"] == "Size"
    )
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            base, headers=headers, json={"surface": "grid", "base_revision": 1}
        )
        session_id = created.json()["session"]["session_id"]
        color_edited = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 1, "operation": {
                "type": "option_values_edit",
                "change_id": color_row["change_id"],
                "value_path": color_row["value_path"],
                "action": "add",
                "value": "Green",
            }},
        )
        size_edited = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 2, "operation": {
                "type": "option_values_edit",
                "change_id": size_row["change_id"],
                "value_path": size_row["value_path"],
                "action": "reorder",
                "values": ["L", "M", "S"],
            }},
        )
        current = size_edited.json()["session"]["working_plan"]["items"][0]
        current_color = next(
            row for row in current["review_changes"]
            if row["scope"] == "option" and row["subject"] == "Color"
        )
        color_reverted = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 3, "operation": {
                "type": "value_revert",
                "change_id": current_color["change_id"],
                "value_path": current_color["value_path"],
            }},
        )
        current = color_reverted.json()["session"]["working_plan"]["items"][0]
        current_size = next(
            row for row in current["review_changes"]
            if row["scope"] == "option" and row["subject"] == "Size"
        )
        size_reverted = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 4, "operation": {
                "type": "value_revert",
                "change_id": current_size["change_id"],
                "value_path": current_size["value_path"],
            }},
        )

    assert color_edited.status_code == 200 and size_edited.status_code == 200
    assert color_reverted.status_code == 200, color_reverted.text
    assert size_reverted.status_code == 200, size_reverted.text
    product = size_reverted.json()["session"]["pending_products"][0]
    assert product["options"][0]["values"] == ["Red", "Blue"]
    assert product["options"][1]["values"] == ["S", "M", "L"]
    assert all(variant["option1"] != "Green" for variant in product["variants"])


@pytest.mark.asyncio
async def test_reset_clears_structural_baselines_before_new_vendor_and_option_edits():
    _service, shop, draft_id, item = await _seed_session("reset-baseline")
    color_row = _option_row(item)
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            base, headers=headers, json={"surface": "grid", "base_revision": 1}
        )
        session_id = created.json()["session"]["session_id"]
        first_option_edit = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 1, "operation": {
                "type": "option_values_edit",
                "change_id": color_row["change_id"],
                "value_path": color_row["value_path"],
                "action": "add",
                "value": "First Green",
            }},
        )
        reset = await client.post(
            f"{base}/{session_id}/reset",
            headers=headers,
            json={"expected_edit_revision": 2},
        )
        reset_item = reset.json()["session"]["working_plan"]["items"][0]
        vendor_row = next(
            row for row in reset_item["review_changes"]
            if row["field"] == "vendor"
        )
        reset_color = _option_row(reset_item)
        vendor_edited = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 3, "operation": {
                "type": "value_edit",
                "change_id": vendor_row["change_id"],
                "value_path": vendor_row["value_path"],
                "value": "Post-reset vendor",
            }},
        )
        second_option_edit = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 4, "operation": {
                "type": "option_values_edit",
                "change_id": reset_color["change_id"],
                "value_path": reset_color["value_path"],
                "action": "add",
                "value": "Second Green",
            }},
        )
        edited_color = _option_row(
            second_option_edit.json()["session"]["working_plan"]["items"][0]
        )
        reverted = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 5, "operation": {
                "type": "value_revert",
                "change_id": edited_color["change_id"],
                "value_path": edited_color["value_path"],
            }},
        )

    assert first_option_edit.status_code == 200 and reset.status_code == 200
    reset_plan = reset.json()["session"]["working_plan"]
    assert "_option_edit_bases" not in reset_plan
    assert "_option_edit_initials" not in reset_plan
    assert vendor_edited.status_code == 200 and second_option_edit.status_code == 200
    assert reverted.status_code == 200, reverted.text
    product = reverted.json()["session"]["pending_products"][0]
    assert product["vendor"] == "Post-reset vendor"
    assert product["options"][0]["values"] == ["Red", "Blue"]
    assert all(
        variant["option1"] not in {"First Green", "Second Green"}
        for variant in product["variants"]
    )


@pytest.mark.asyncio
async def test_rename_preserves_dependent_row_identity_inclusion_and_provenance():
    _service, shop, draft_id, item = await _seed_session("rename-identity")
    color_row = _option_row(item)
    dependent = next(
        row for row in item["review_changes"]
        if row["scope"] == "variant"
        and "Color: Blue" in row["subject"]
        and row["field"] == "price"
    )
    dependent.update({
        "included": False,
        "origin": "source",
        "source_ref": {"value_path": dependent["value_path"], "sheet": "Products", "cell": "C2"},
        "original_proposed": dependent["proposed"],
        "original_origin": "source",
        "original_source_ref": {"value_path": dependent["value_path"], "sheet": "Products", "cell": "C2"},
    })
    original = deepcopy(dependent)
    get_app_context().services.supabase._service.mutation_plans[
        "option-rename-identity-plan"
    ]["items"] = [item]

    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(base, headers=headers, json={"surface": "grid", "base_revision": 1})
        session_id = created.json()["session"]["session_id"]
        renamed = await client.patch(
            f"{base}/{session_id}", headers=headers,
            json={"expected_edit_revision": 1, "operation": {
                "type": "option_values_edit", "change_id": color_row["change_id"],
                "value_path": color_row["value_path"], "action": "rename",
                "from": "blue", "to": "Azure",
            }},
        )

    assert renamed.status_code == 200, renamed.text
    azure = next(
        row for row in renamed.json()["session"]["working_plan"]["items"][0]["review_changes"]
        if row["scope"] == "variant"
        and "Color: Azure" in row["subject"]
        and row["field"] == "price"
        and "Size: S" in row["subject"]
    )
    assert azure["change_id"] == original["change_id"]
    assert azure["included"] is False
    assert azure["origin"] == "source"
    assert azure["source_ref"] == original["source_ref"]
    assert azure["original_proposed"] == original["original_proposed"]
    assert azure["original_origin"] == original["original_origin"]
    assert azure["original_source_ref"] == original["original_source_ref"]


@pytest.mark.asyncio
async def test_rename_identity_does_not_parse_display_subject_delimiters():
    service = get_app_context().services.supabase._service
    live, source, proposed = _option_fixture()
    source["options"][0]["values"][1] = "Blue / Slate"
    proposed["productOptions"][0]["values"][1]["name"] = "Blue / Slate"
    for product in (source, proposed):
        for variant in product["variants"]:
            if variant.get("option1") == "Blue":
                variant["option1"] = "Blue / Slate"
            for key in ("selected_options", "optionValues"):
                for pair in variant.get(key, []):
                    if pair.get("value") == "Blue":
                        pair["value"] = "Blue / Slate"
                    if pair.get("name") == "Blue":
                        pair["name"] = "Blue / Slate"
    item = build_review_plan_item(
        item_id="delimiter-identity-item",
        currency_code="CAD",
        source_index=0,
        source_product=source,
        live_snapshot=live,
        proposed=proposed,
        operation="update",
    )
    color_row = _option_row(item)
    dependent = next(
        row for row in item["review_changes"]
        if row["scope"] == "variant"
        and row["field"] == "price"
        and row.get("subject_identity", {}).get("values") == ["blue / slate", "s"]
    )
    original_change_id = dependent["change_id"]
    dependent["included"] = False
    dependent.pop("subject_identity")
    shop = "option-delimiter-identity.myshopify.com"
    draft_id = "option-delimiter-identity-draft"
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 1,
        "products": [source],
    }
    service.mutation_plans["option-delimiter-identity-plan"] = {
        "plan_id": "option-delimiter-identity-plan",
        "draft_id": draft_id,
        "shop_domain": shop,
        "version": 1,
        "status": "review",
        "review_contract_version": 2,
        "items": [item],
    }
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            base, headers=headers, json={"surface": "grid", "base_revision": 1}
        )
        hydrated_dependent = next(
            row
            for row in created.json()["session"]["working_plan"]["items"][0][
                "review_changes"
            ]
            if row["change_id"] == original_change_id
        )
        renamed = await client.patch(
            f"{base}/{created.json()['session']['session_id']}",
            headers=headers,
            json={"expected_edit_revision": 1, "operation": {
                "type": "option_values_edit",
                "change_id": color_row["change_id"],
                "value_path": color_row["value_path"],
                "action": "rename",
                "from": "Blue / Slate",
                "to": "Azure / Mist",
            }},
        )

    assert renamed.status_code == 200, renamed.text
    assert hydrated_dependent["subject_identity"]["values"] == [
        "blue / slate",
        "s",
    ]
    renamed_dependent = next(
        row
        for row in renamed.json()["session"]["working_plan"]["items"][0][
            "review_changes"
        ]
        if row["scope"] == "variant"
        and row["field"] == "price"
        and row.get("subject_identity", {}).get("values")
        == ["azure / mist", "s"]
    )
    assert renamed_dependent["change_id"] == original_change_id
    assert renamed_dependent["included"] is False


@pytest.mark.asyncio
async def test_option_row_remains_operable_when_structural_edit_reaches_live_parity():
    service = get_app_context().services.supabase._service
    live, source, proposed = _option_fixture()
    live["options"][0]["optionValues"].append({"name": "Blue"})
    source["options"][0]["values"] = ["Red", "Azure"]
    proposed["productOptions"][0]["values"] = [{"name": "Red"}, {"name": "Azure"}]
    for product in (source, proposed):
        for variant in product["variants"]:
            if variant.get("option1") == "Blue":
                variant["option1"] = "Azure"
            for key in ("selected_options", "optionValues"):
                for pair in variant.get(key, []):
                    if pair.get("value") == "Blue":
                        pair["value"] = "Azure"
                    if pair.get("name") == "Blue":
                        pair["name"] = "Azure"
    item = build_review_plan_item(
        item_id="live-parity-item", currency_code="CAD", source_index=0,
        source_product=source, live_snapshot=live, proposed=proposed,
        operation="update",
    )
    row = _option_row(item)
    shop = "option-live-parity.myshopify.com"
    draft_id = "option-live-parity-draft"
    service.product_drafts[draft_id] = {
        "draft_id": draft_id, "shop_domain": shop, "revision": 1,
        "products": [source],
    }
    service.mutation_plans["option-live-parity-plan"] = {
        "plan_id": "option-live-parity-plan", "draft_id": draft_id,
        "shop_domain": shop, "version": 1, "status": "review",
        "review_contract_version": 2, "items": [item],
    }
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(base, headers=headers, json={"surface": "grid", "base_revision": 1})
        session_id = created.json()["session"]["session_id"]
        parity = await client.patch(
            f"{base}/{session_id}", headers=headers,
            json={"expected_edit_revision": 1, "operation": {
                "type": "option_values_edit", "change_id": row["change_id"],
                "value_path": row["value_path"], "action": "rename",
                "from": "Azure", "to": "Blue",
            }},
        )
        parity_item = parity.json()["session"]["working_plan"]["items"][0]
        anchor = _option_row(parity_item)
        reordered = await client.patch(
            f"{base}/{session_id}", headers=headers,
            json={"expected_edit_revision": 2, "operation": {
                "type": "option_values_edit", "change_id": anchor["change_id"],
                "value_path": anchor["value_path"], "action": "reorder",
                "values": ["Blue", "Red"],
            }},
        )

    assert parity.status_code == 200, parity.text
    assert anchor["change_id"] == row["change_id"]
    assert anchor["editable"] is True
    assert anchor["edit_schema"]["editor"] == "option_values"
    assert anchor["proposed"] == ["Red", "Blue"]
    assert reordered.status_code == 200, reordered.text
    assert _option_row(reordered.json()["session"]["working_plan"]["items"][0])["proposed"] == ["Blue", "Red"]


@pytest.mark.asyncio
async def test_apply_after_remove_never_reintroduces_omitted_variants(monkeypatch):
    import api.agents.review_edit_sessions as sessions_api

    service, shop, draft_id, item = await _seed_session("apply-remove")
    row = _option_row(item)

    async def fake_create_plan(draft_id, request, payload, ctx):
        saved = ctx.supabase.drafts.get_product_draft(
            draft_id, shop_domain=shop
        )
        assert saved["products"][0]["options"][0]["values"] == ["Red"]
        assert all(
            variant["option1"] != "Blue"
            for variant in saved["products"][0]["variants"]
        )
        reintroducing = deepcopy(item)
        assert any(
            pair["name"] == "Blue"
            for variant in reintroducing["proposed"]["variants"]
            for pair in variant["optionValues"]
            if pair["optionName"] == "Color"
        )
        return {
            "plan": {
                "plan_id": "apply-remove-refreshed",
                "draft_id": draft_id,
                "shop_domain": shop,
                "draft_revision": 2,
                "currency_code": "CAD",
                "review_contract_version": 2,
                "items": [reintroducing],
            }
        }

    monkeypatch.setattr(sessions_api, "create_mutation_plan", fake_create_plan)
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            base,
            headers=headers,
            json={"surface": "grid", "base_revision": 1},
        )
        session_id = created.json()["session"]["session_id"]
        removed = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={
                "expected_edit_revision": 1,
                "operation": {
                    "type": "option_values_edit",
                    "change_id": row["change_id"],
                    "value_path": row["value_path"],
                    "action": "remove",
                    "value": "Blue",
                },
            },
        )
        applied = await client.post(
            f"{base}/{session_id}/apply",
            headers=headers,
            json={"expected_edit_revision": 2},
        )

    assert removed.status_code == 200, removed.text
    assert applied.status_code == 200, applied.text
    refreshed = applied.json()["plan"]["items"][0]["proposed"]
    color = next(
        option
        for option in refreshed["productOptions"]
        if option["name"] == "Color"
    )
    assert [value["name"] for value in color["values"]] == ["Red"]
    assert all(
        next(
            pair["name"]
            for pair in variant["optionValues"]
            if pair["optionName"] == "Color"
        )
        != "Blue"
        for variant in refreshed["variants"]
    )
    stored = service.mutation_plans["apply-remove-refreshed"]["items"][0][
        "proposed"
    ]
    assert stored == refreshed


@pytest.mark.asyncio
async def test_invalid_stale_and_forged_option_operations_return_field_errors_atomically():
    _service, shop, draft_id, item = await _seed_session("validation")
    row = _option_row(item)
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(base, headers=headers, json={"surface": "grid", "base_revision": 1})
        session_id = created.json()["session"]["session_id"]
        duplicate = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 1, "operation": {
                "type": "option_values_edit", "change_id": row["change_id"],
                "value_path": row["value_path"], "action": "add", "value": "red",
            }},
        )
        stale = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 99, "operation": {
                "type": "option_values_edit", "change_id": row["change_id"],
                "value_path": row["value_path"], "action": "add", "value": "Green",
            }},
        )
        forged_scalar = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 1, "operation": {
                "type": "value_edit", "change_id": row["change_id"],
                "value_path": row["value_path"], "value": ["Red", "Green"],
            }},
        )
        active = await client.get(base, headers=headers)

    assert duplicate.json()["detail"]["field_errors"][0]["code"] == "DUPLICATE_OPTION_VALUE"
    assert stale.json()["detail"]["field_errors"][0]["code"] == "STALE_EDIT_REVISION"
    assert forged_scalar.json()["detail"]["field_errors"][0]["code"] == "OPTION_VALUES_OPERATION_REQUIRED"
    assert active.json()["session"]["edit_revision"] == 1
    assert active.json()["session"]["pending_products"][0]["options"][0]["values"] == ["Red", "Blue"]
    assert active.json()["session"]["history"] == []


@pytest.mark.asyncio
async def test_default_title_targetless_row_never_reaches_raw_option_error():
    service = get_app_context().services.supabase._service
    shop = "default-title-regression.myshopify.com"
    draft_id = "default-title-regression-draft"
    product = {
        "title": "Converted",
        "options": [{"name": "Color", "values": ["Red"]}],
        "variants": [{"option1": "Red"}],
    }
    plan = normalize_review_plan_rows({
        "plan_id": "default-title-regression-plan",
        "draft_id": draft_id,
        "shop_domain": shop,
        "version": 1,
        "status": "review",
        "review_contract_version": 2,
        "items": [{
            "item_id": "default-title-item",
            "source_index": 0,
            "proposed": {
                "title": "Converted",
                "productOptions": [{"name": "Color", "values": [{"name": "Red"}]}],
                "variants": [{"optionValues": [{"optionName": "Color", "name": "Red"}]}],
            },
            "review_changes": [{
                "change_id": "removed-default-title",
                "value_path": "option[title].values",
                "scope": "option",
                "subject": "Title",
                "field": "values",
                "proposed": None,
                "disposition": "applicable",
                "required": True,
            }],
        }],
    })
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 1,
        "products": [product],
    }
    service.mutation_plans["default-title-regression-plan"] = plan
    row = plan["items"][0]["review_changes"][0]
    assert row["presentation"] == "hidden"
    assert row["editable"] is False

    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(base, headers=headers, json={"surface": "grid", "base_revision": 1})
        session_id = created.json()["session"]["session_id"]
        rejected = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={"expected_edit_revision": 1, "operation": {
                "type": "value_edit",
                "change_id": row["change_id"],
                "value_path": row["value_path"],
                "value": ["Red"],
            }},
        )

    assert rejected.status_code == 422
    detail = rejected.json()["detail"]
    assert detail["code"] == "REVIEW_FIELD_VALIDATION_FAILED"
    assert detail["field_errors"][0]["code"] == "REVIEW_FIELD_READ_ONLY"
    assert "Option is unavailable" not in rejected.text
