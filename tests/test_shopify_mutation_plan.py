from application.domain.shopify_mutation_plan import (
    applicable_changes,
    build_review_plan_item,
    canonical_product,
    merge_tags,
    merge_structured_product,
    materialize_execution_proposed,
    normalize_review_plan_rows,
    preserve_blank_fields,
    review_changes,
    validate_product,
)
from application.domain.shopify_product_normalization import build_product_set_input


def _single_variant_product(cost):
    inventory_item = {"unitCost": cost} if cost is not ... else {}
    return {
        "options": [{"name": "Title", "optionValues": [{"name": "Default Title"}]}],
        "variants": {
            "nodes": [
                {
                    "id": "gid://shopify/ProductVariant/1",
                    "selectedOptions": [{"name": "Title", "value": "Default Title"}],
                    "inventoryItem": inventory_item,
                }
            ]
        },
    }


def test_invalid_status_is_blocked_before_transport():
    blockers, _ = validate_product(
        {"title": "Hat", "status": "published"}, creating=True
    )
    assert "INVALID_STATUS" in {item["code"] for item in blockers}


def test_missing_sku_and_price_have_required_editable_blocked_rows():
    imported = {
        "title": "Tee",
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [{"option1": "Default Title"}],
    }
    proposed, _, _ = merge_structured_product(None, imported, {"title": "Tee"})
    plan_item = build_review_plan_item(
        item_id="item",
        currency_code="USD",
        source_index=0,
        source_product=imported,
        live_snapshot=None,
        proposed=proposed,
        operation="create",
        blockers=[
            {"code": "SKU_REQUIRED", "field": "variants[0].sku"},
            {"code": "PRICE_REQUIRED", "field": "variants[0].price"},
        ],
    )

    required = {
        row["field"]: row
        for row in plan_item["review_changes"]
        if row["field"] in {"sku", "price"}
    }
    assert set(required) == {"sku", "price"}
    assert all(row["disposition"] == "blocked" for row in required.values())
    assert all(row["required"] and row["editable"] for row in required.values())


def test_review_rows_have_stable_ids_paths_and_truthful_provenance():
    imported = {
        "handle": "catalog-hat",
        "title": "New hat",
        "vendor": "Merchant edit",
        "source_refs": [
            {"field": "title", "sheet": "Products", "cell": "A2"},
            {"field": "vendor", "sheet": "Products", "cell": "B2"},
        ],
        "value_origins": {"vendor": "merchant_edited"},
    }
    first = review_changes(
        {"title": "Old hat", "vendor": "Old vendor"},
        imported,
        {"title": "New hat", "vendor": "Merchant edit"},
    )
    second = review_changes(
        {"title": "Old hat", "vendor": "Old vendor"},
        imported,
        {"title": "New hat", "vendor": "Merchant edit"},
    )
    assert [row["change_id"] for row in first] == [row["change_id"] for row in second]
    title = next(row for row in first if row["field"] == "title")
    vendor = next(row for row in first if row["field"] == "vendor")
    assert title["value_path"] == "title"
    assert title["origin"] == "source"
    assert title["source_ref"]["cell"] == "A2"
    assert vendor["origin"] == "merchant_edited"
    assert "source_ref" not in vendor


def test_variant_review_rows_match_evidence_by_exact_value_path_only():
    imported = {
        "title": "Tee", "options": [{"name": "Size", "values": ["S", "M"]}],
        "variants": [
            {"option1": "S", "sku": "TEE-S", "price": 10},
            {"option1": "M", "sku": "TEE-M", "price": 12},
        ],
        "source_refs": [
            {"field": "price", "value_path": "variant[size-s].price", "source_file_id": "f", "sheet": "Products", "cell": "C2"},
            {"field": "price", "value_path": "variant[size-m].price", "source_file_id": "f", "sheet": "Products", "cell": "C3"},
            {"field": "sku", "sheet": "Products", "cell": "B2"},
        ],
    }
    proposed, _, _ = merge_structured_product(None, imported, {"title": "Tee"})
    rows = review_changes(None, imported, proposed)
    prices = {row["value_path"]: row for row in rows if row["field"] == "price"}
    assert prices["variant[size-s].price"]["source_ref"]["cell"] == "C2"
    assert prices["variant[size-m].price"]["source_ref"]["cell"] == "C3"
    assert all(row["origin"] == "unknown" and "source_ref" not in row for row in rows if row["field"] == "sku")


def test_ai_origins_resolve_for_every_proposed_value_path_category():
    imported = {
        "title": "AI planter",
        "options": [{"name": "Size", "values": ["Small"]}],
        "variants": [
            {
                "option_values": [{"option_name": "Size", "name": "Small"}],
                "price": "30.99",
                "inventory_quantity": 7,
            }
        ],
        "metafields": [
            {
                "namespace": "custom",
                "key": "details",
                "value": "AI description",
                "type": "single_line_text_field",
            }
        ],
        "value_origins": {
            "TITLE": "ai_assisted",
            "options[Size].values": "ai_assisted",
            "variants[Size: Small].price": "ai_assisted",
            "metafields[custom.details].value": "ai_assisted",
            "inventory[Size: Small].inventory_quantity": "ai_assisted",
        },
    }
    proposed = preserve_blank_fields({}, imported)
    proposed, blockers, unapplied = merge_structured_product(None, imported, proposed)

    rows = review_changes(None, imported, proposed, blockers, unapplied)

    expected_paths = {
        "title",
        "option[size].values",
        "variant[size-small].price",
        "metafield[custom-details].value",
        "inventory[size-small].inventory_quantity",
    }
    by_path = {row["value_path"]: row for row in rows}
    assert expected_paths <= by_path.keys()
    assert all(by_path[path]["origin"] == "ai_assisted" for path in expected_paths)
    assert all("source_ref" not in by_path[path] for path in expected_paths)


def test_origin_resolution_keeps_source_refs_exact_and_unknown_fallback_safe():
    imported = {
        "title": "Tee",
        "options": [{"name": "Size", "values": ["S"]}],
        "variants": [
            {
                "option_values": [{"option_name": "Size", "name": "S"}],
                "price": "12",
                "sku": "TEE-S",
            }
        ],
        "source_refs": [
            {
                "field": "price",
                "value_path": "variant[size-s].price",
                "sheet": "Products",
                "cell": "C2",
            },
            {
                "field": "sku",
                "value_path": "variants[Size: S].sku",
                "sheet": "Products",
                "cell": "B2",
            },
        ],
        "value_origins": {
            "variants[Size: S].sku": "not-a-valid-origin",
        },
    }
    proposed, blockers, unapplied = merge_structured_product(None, imported, {})

    rows = review_changes(None, imported, proposed, blockers, unapplied)
    price = next(row for row in rows if row["field"] == "price")
    sku = next(row for row in rows if row["field"] == "sku")

    assert price["origin"] == "source"
    assert price["source_ref"]["cell"] == "C2"
    assert sku["origin"] == "unknown"
    assert "source_ref" not in sku


def test_execution_payload_reverts_excluded_updates_and_omits_excluded_create_values():
    update_item = {
        "live_snapshot": {"id": "gid://shopify/Product/1", "title": "Old", "vendor": "Keep"},
        "proposed": {"id": "gid://shopify/Product/1", "title": "New", "vendor": "Replace"},
        "review_changes": [
            {"scope": "product", "field": "title", "current": "Old", "included": True, "disposition": "applicable"},
            {"scope": "product", "field": "vendor", "current": "Keep", "included": False, "disposition": "applicable"},
        ],
    }
    create_item = {
        "live_snapshot": None,
        "proposed": {"title": "New", "vendor": "Optional"},
        "review_changes": [
            {"scope": "product", "field": "title", "current": None, "included": True, "disposition": "applicable"},
            {"scope": "product", "field": "vendor", "current": None, "included": False, "disposition": "applicable"},
        ],
    }
    assert materialize_execution_proposed(update_item) == {
        "id": "gid://shopify/Product/1", "title": "New", "vendor": "Keep"
    }
    assert materialize_execution_proposed(create_item) == {"title": "New"}


def test_create_defaults_are_valid_and_deferred_fields_remain_unapplied():
    blockers, unapplied = validate_product(
        {"title": "Hat", "internationalPrices": {"CA": "12.00"}}, creating=True
    )
    assert blockers == []
    assert unapplied[0]["code"] == "TODO_MARKETS_PRICES"


def test_preserve_blank_and_union_tags():
    proposed = preserve_blank_fields(
        {
            "vendor": "Existing",
            "tags": ["Cotton", "Sale"],
            "seo": {"title": "Old", "description": "Keep"},
        },
        {"vendor": "", "tags": ["sale", "New"], "seo_title": "New"},
    )
    assert proposed["vendor"] == "Existing"
    assert proposed["tags"] == ["Cotton", "Sale", "New"]
    assert proposed["seo"] == {"title": "New", "description": "Keep"}
    assert merge_tags("A, B", ["b", "C"]) == ["A", "B", "C"]


def test_option_and_variant_data_is_structured_not_wholly_deferred():
    blockers, unapplied = validate_product(
        {"title": "Hat", "options": [], "variants": []}, creating=True
    )
    assert blockers == []
    assert unapplied == []
    assert canonical_product(
        {"title": "Hat", "variants": [{"price": "9"}], "source_refs": [1]}
    ) == {"title": "Hat"}


def test_replaces_default_placeholder_with_real_options_and_variants():
    live = {
        "options": [{"name": "Title", "optionValues": [{"name": "Default Title"}]}],
        "variants": {
            "nodes": [
                {
                    "id": "gid://shopify/ProductVariant/default",
                    "sku": "OLD",
                    "price": "10.00",
                    "selectedOptions": [{"name": "Title", "value": "Default Title"}],
                }
            ]
        },
    }
    imported = {
        "options": [{"name": " Color ", "values": [" Red ", "Blue"]}],
        "variants": [
            {
                "option_values": [{"option_name": "color", "name": "red"}],
                "sku": "RED",
                "price": "12",
            },
            {
                "option_values": [{"option_name": "Color", "name": "Blue"}],
                "sku": "BLUE",
                "price": "13",
            },
        ],
    }
    proposed, blockers, unapplied = merge_structured_product(live, imported, {})
    assert blockers == []
    assert unapplied == []
    assert proposed["productOptions"] == [
        {
            "name": "Color",
            "position": 1,
            "values": [{"name": "Red"}, {"name": "Blue"}],
        }
    ]
    assert len(proposed["variants"]) == 2
    assert all("id" not in variant for variant in proposed["variants"])


def test_compatible_matrix_merge_retains_live_only_matches_ids_and_adds_missing():
    live = {
        "options": [{"name": "Size", "optionValues": [{"name": "S"}, {"name": "M"}]}],
        "variants": {
            "nodes": [
                {
                    "id": "gid://shopify/ProductVariant/S",
                    "sku": "LIVE-S",
                    "price": "10",
                    "selectedOptions": [{"name": "Size", "value": "S"}],
                },
                {
                    "id": "gid://shopify/ProductVariant/M",
                    "sku": "LIVE-M",
                    "price": "10",
                    "barcode": "OLD",
                    "taxable": False,
                    "taxCode": "OLD-CODE",
                    "inventoryPolicy": "DENY",
                    "inventoryItem": {
                        "cost": "5",
                        "tracked": False,
                        "requiresShipping": False,
                        "measurement": {"weight": {"value": 1, "unit": "KILOGRAMS"}},
                    },
                    "selectedOptions": [{"name": "Size", "value": "M"}],
                },
            ]
        },
    }
    imported = {
        "options": [{"name": " size ", "values": ["m", "L"]}],
        "variants": [
            {
                "option_values": [{"option_name": "SIZE", "name": " m "}],
                "sku": "IMPORT-M",
                "price": "15",
                "compare_at_price": "20",
                "barcode": "NEW",
                "taxable": True,
                "tax_code": "NEW-CODE",
                "inventory_policy": "continue",
                "cost": "6",
                "tracked": True,
                "requires_shipping": True,
                "weight": "500",
                "weight_unit": "g",
            },
            {
                "option_values": [{"option_name": "Size", "name": "L"}],
                "sku": "IMPORT-L",
                "price": "16",
            },
        ],
    }
    proposed, blockers, _ = merge_structured_product(live, imported, {})
    assert blockers == []
    assert len(proposed["variants"]) == 3
    by_value = {
        variant["optionValues"][0]["name"].casefold(): variant
        for variant in proposed["variants"]
    }
    assert by_value["s"]["id"].endswith("/S")
    assert by_value["s"]["sku"] == "LIVE-S"
    assert by_value["m"]["id"].endswith("/M")
    assert by_value["m"]["sku"] == "IMPORT-M"
    assert by_value["m"]["price"] == "15"
    assert by_value["m"]["compareAtPrice"] == "20"
    assert by_value["m"]["barcode"] == "NEW"
    assert by_value["m"]["taxable"] is True
    assert by_value["m"]["taxCode"] == "NEW-CODE"
    assert by_value["m"]["inventoryPolicy"] == "CONTINUE"
    assert by_value["m"]["inventoryItem"] == {
        "cost": "6",
        "tracked": True,
        "requiresShipping": True,
        "measurement": {"weight": {"value": 500.0, "unit": "GRAMS"}},
    }
    assert "id" not in by_value["l"]


def test_structured_validation_blocks_schema_duplicates_and_defers_inventory_granularly():
    live = {
        "options": [{"name": "Color", "optionValues": [{"name": "Red"}]}],
        "variants": {
            "nodes": [
                {"id": "v1", "selectedOptions": [{"name": "Color", "value": "Red"}]}
            ]
        },
    }
    imported = {
        "options": [{"name": "Size", "values": ["S"]}],
        "variants": [
            {
                "option_values": [{"option_name": "Color", "name": "S"}],
                "inventory_quantity": 4,
                "image": "x",
            },
            {"option_values": [{"option_name": "Color", "name": "S"}]},
        ],
    }
    _, blockers, unapplied = merge_structured_product(live, imported, {})
    assert {item["code"] for item in blockers} >= {
        "INCOMPATIBLE_OPTION_SCHEMA",
        "DUPLICATE_VARIANT_COMBINATION",
    }
    assert {item["field"] for item in unapplied} == {
        "variants[0].inventory_quantity",
        "variants[0].image",
    }


def test_metafields_are_upserts_and_diff_only_imported_keys():
    live = {
        "metafields": {
            "nodes": [
                {
                    "namespace": "custom",
                    "key": "material",
                    "value": "wool",
                    "type": "single_line_text_field",
                },
                {
                    "namespace": "custom",
                    "key": "unrelated",
                    "value": "keep",
                    "type": "single_line_text_field",
                },
            ]
        }
    }
    imported = {
        "metafields": [
            {
                "namespace": " custom ",
                "key": "material",
                "value": "cotton",
                "type": "single_line_text_field",
            }
        ]
    }
    proposed, blockers, _ = merge_structured_product(live, imported, {})
    assert blockers == []
    assert len(proposed["metafields"]) == 1
    assert applicable_changes(live, proposed)[0] == {
        "field": "metafields",
        "current": {"matching": 0},
        "proposed": {"upserts": 1},
    }


def test_option_and_merged_variant_limits_are_blocked():
    too_many_options = {
        "options": [{"name": f"Option {index}", "values": ["X"]} for index in range(4)],
        "variants": [
            {
                "option_values": [
                    {"option_name": f"Option {index}", "name": "X"}
                    for index in range(4)
                ]
            }
        ],
    }
    _, option_blockers, _ = merge_structured_product(None, too_many_options, {})
    assert "OPTION_LIMIT_EXCEEDED" in {item["code"] for item in option_blockers}

    too_many_variants = {
        "options": [
            {"name": "Number", "values": [str(index) for index in range(2049)]}
        ],
        "variants": [
            {"option_values": [{"option_name": "Number", "name": str(index)}]}
            for index in range(2049)
        ],
    }
    _, variant_blockers, _ = merge_structured_product(None, too_many_variants, {})
    assert "VARIANT_LIMIT_EXCEEDED" in {item["code"] for item in variant_blockers}


def test_genuinely_unchanged_structured_state_has_no_diff():
    live = {
        "options": [{"name": "Size", "optionValues": [{"name": "S"}]}],
        "variants": {
            "nodes": [
                {
                    "id": "gid://shopify/ProductVariant/1",
                    "sku": "TEE-S",
                    "price": "20",
                    "selectedOptions": [{"name": "Size", "value": "S"}],
                }
            ]
        },
    }
    imported = {
        "options": [{"name": " size ", "values": ["s"]}],
        "variants": [
            {
                "option_values": [{"option_name": "Size", "name": "S"}],
                "sku": "TEE-S",
                "price": "20",
            }
        ],
    }
    proposed, blockers, _ = merge_structured_product(live, imported, {})
    assert blockers == []
    assert applicable_changes(live, proposed) == []


def test_live_unit_cost_normalizes_to_mutation_input_cost_without_false_diff():
    live = _single_variant_product({"amount": "8.00"})
    imported = {
        "variants": [
            {
                "option_values": [{"option_name": "Title", "name": "Default Title"}],
                "cost": "8.00",
            }
        ]
    }

    proposed, blockers, _ = merge_structured_product(live, imported, {})

    assert blockers == []
    assert proposed["variants"][0]["inventoryItem"]["cost"] == "8.00"
    assert applicable_changes(live, proposed) == []


def test_changed_imported_cost_uses_inventory_item_cost_mutation_shape():
    live = _single_variant_product({"amount": "8.00"})
    imported = {
        "variants": [
            {
                "option_values": [{"option_name": "Title", "name": "Default Title"}],
                "cost": "9.50",
            }
        ]
    }

    proposed, blockers, _ = merge_structured_product(live, imported, {})

    assert blockers == []
    assert proposed["variants"][0]["inventoryItem"] == {"cost": "9.50"}
    payload = build_product_set_input(proposed)
    assert payload["variants"][0]["inventoryItem"] == {"cost": "9.50"}
    assert {change["field"] for change in applicable_changes(live, proposed)} == {
        "variants"
    }


def test_null_or_missing_live_unit_cost_does_not_create_cost_mutation_input():
    imported = {
        "variants": [
            {"option_values": [{"option_name": "Title", "name": "Default Title"}]}
        ]
    }

    for unit_cost in (None, ...):
        live = _single_variant_product(unit_cost)
        proposed, blockers, _ = merge_structured_product(live, imported, {})

        assert blockers == []
        assert "inventoryItem" not in proposed["variants"][0]
        assert applicable_changes(live, proposed) == []


def test_safe_core_diff_and_unlisted_status():
    blockers, _ = validate_product(
        {"title": "Hat", "status": "unlisted"}, creating=True
    )
    assert blockers == []
    assert applicable_changes(
        {"title": "Hat", "status": "UNLISTED", "vendor": "A"},
        {"title": "Hat", "status": "unlisted", "vendor": "B", "inventory": {"x": 1}},
    ) == [{"field": "vendor", "current": "A", "proposed": "B"}]


def test_extracted_and_live_aliases_share_one_core_representation():
    extracted = {
        "title": " Hat ",
        "body_html": "<p>New</p>",
        "product_category": "Accessories",
        "seo_title": "New SEO title",
        "seo_description": "New SEO description",
    }
    assert canonical_product(extracted) == {
        "title": "Hat",
        "descriptionHtml": "<p>New</p>",
        "productType": "Accessories",
        "seo": {"title": "New SEO title", "description": "New SEO description"},
    }
    changes = applicable_changes(
        {
            "title": "Hat",
            "descriptionHtml": "<p>Old</p>",
            "productType": "Clothing",
            "seo": {"title": "Old SEO title", "description": "Old SEO description"},
        },
        extracted,
    )
    assert {change["field"] for change in changes} == {
        "descriptionHtml",
        "productType",
        "seo",
    }


def test_exact_review_rows_cover_options_variant_fields_cost_and_metafields():
    live = {
        "title": "Planter",
        "options": [{"name": "Size", "optionValues": [{"name": "Small"}]}],
        "variants": {
            "nodes": [
                {
                    "id": "gid://shopify/ProductVariant/1",
                    "selectedOptions": [{"name": "Size", "value": "Small"}],
                    "price": "0.00",
                    "sku": "OLD-SKU",
                    "inventoryItem": {"unitCost": {"amount": "8.00"}},
                }
            ]
        },
        "metafields": {
            "nodes": [
                {
                    "namespace": "custom",
                    "key": "details",
                    "value": "old",
                    "type": "multi_line_text_field",
                }
            ]
        },
    }
    imported = {
        "title": "Planter",
        "options": [{"name": "Size", "values": ["Small", "Large"]}],
        "variants": [
            {
                "option_values": [{"option_name": "Size", "name": "Small"}],
                "price": "30.99",
                "sku": "NEW-SKU",
                "cost": "9.50",
            },
            {
                "option_values": [{"option_name": "Size", "name": "Large"}],
                "price": "34.99",
                "sku": "LARGE",
            },
        ],
        "metafields": [
            {
                "namespace": "custom",
                "key": "details",
                "value": {"material": "ceramic"},
                "type": "json",
            }
        ],
    }
    proposed, blockers, unapplied = merge_structured_product(live, imported, {})

    rows = review_changes(live, imported, proposed, blockers, unapplied)

    assert blockers == []
    assert any(
        row["scope"] == "option" and row["proposed"] == ["Small", "Large"]
        for row in rows
    )
    assert any(
        row["field"] == "price"
        and row["current"] == "0.00"
        and row["proposed"] == "30.99"
        and row["value_type"] == "money"
        for row in rows
    )
    assert any(row["field"] == "sku" and row["proposed"] == "NEW-SKU" for row in rows)
    assert any(
        row["field"] == "inventoryItem.cost" and row["proposed"] == "9.50"
        for row in rows
    )
    assert any(
        row["scope"] == "variant"
        and row["subject"] == "Size: Large"
        and row["change_kind"] == "added"
        for row in rows
    )
    assert any(
        row["scope"] == "metafield"
        and row["subject"] == "custom.details"
        and row["field"] == "value"
        for row in rows
    )


def test_review_exposes_rejected_variant_values_and_deferred_quantities():
    imported = {
        "title": "Blocked planter",
        "options": [{"name": "Size", "values": ["Small"]}],
        "variants": [{"price": "30.99", "sku": "REJECTED", "inventory_quantity": 7}],
    }
    proposed = preserve_blank_fields({}, imported)
    proposed, blockers, unapplied = merge_structured_product(None, imported, proposed)

    rows = review_changes(None, imported, proposed, blockers, unapplied)

    rejected_price = next(
        row
        for row in rows
        if row["field"] == "price" and row["change_kind"] == "rejected"
    )
    assert rejected_price["proposed"] == "30.99"
    assert rejected_price["disposition"] == "blocked"
    assert "INCOMPLETE_VARIANT_COMBINATION" in rejected_price["reason_codes"]
    quantity = next(row for row in rows if row["field"] == "inventory_quantity")
    assert quantity["proposed"] == 7
    assert quantity["disposition"] == "deferred"
    assert quantity["scope"] == "inventory"


def test_review_matches_variant_id_before_options_omits_unchanged_and_reports_removal():
    live = {
        "options": [
            {"name": "Size", "optionValues": [{"name": "Small"}, {"name": "Large"}]}
        ],
        "variants": {
            "nodes": [
                {
                    "id": "v1",
                    "selectedOptions": [{"name": "Size", "value": "Small"}],
                    "sku": "ONE",
                },
                {
                    "id": "v2",
                    "selectedOptions": [{"name": "Size", "value": "Large"}],
                    "sku": "TWO",
                },
            ]
        },
    }
    proposed = {
        "productOptions": [
            {
                "name": "Size",
                "position": 1,
                "values": [{"name": "Small"}, {"name": "Medium"}],
            }
        ],
        "variants": [
            {
                "id": "v1",
                "optionValues": [{"optionName": "Size", "name": "Medium"}],
                "sku": "ONE",
            }
        ],
    }

    rows = review_changes(live, {}, proposed)

    assert not any(
        row["field"] == "sku" and row["subject"] == "Size: Medium" for row in rows
    )
    assert any(
        row["field"] == "sku"
        and row["current"] == "TWO"
        and row["proposed"] is None
        and row["change_kind"] == "removed"
        for row in rows
    )


def test_review_row_presentation_hides_only_applicable_targetless_rows():
    plan = normalize_review_plan_rows(
        {
            "review_contract_version": 2,
            "items": [
                {
                    "proposed": {
                        "title": "Planter",
                        "productOptions": [
                            {"name": "Size", "values": [{"name": "Small"}]}
                        ],
                        "variants": [],
                    },
                    "review_changes": [
                        {
                            "scope": "option",
                            "subject": "Title",
                            "field": "values",
                            "disposition": "applicable",
                            "required": True,
                            "included": True,
                        },
                        {
                            "scope": "variant",
                            "subject": "Size: Rejected",
                            "field": "price",
                            "disposition": "blocked",
                        },
                        {
                            "scope": "inventory",
                            "subject": "Size: Small",
                            "field": "inventory_quantity",
                            "disposition": "deferred",
                        },
                    ],
                }
            ],
        }
    )

    hidden, blocked, deferred = plan["items"][0]["review_changes"]
    assert hidden["presentation"] == "hidden"
    assert hidden["editable"] is False
    assert hidden["required"] is True
    assert hidden["included"] is True
    assert blocked["presentation"] == "merchant"
    assert blocked["editable"] is False
    assert deferred["presentation"] == "merchant"
    assert deferred["editable"] is False


def test_review_row_presentation_protects_technical_metafields_and_allows_merchant_values():
    plan = normalize_review_plan_rows(
        {
            "review_contract_version": 2,
            "items": [
                {
                    "proposed": {
                        "metafields": [
                            {
                                "namespace": "extractor",
                                "key": "inferred_option_candidates",
                                "value": '{"confidence":0.92}',
                                "type": "json",
                            },
                            {
                                "namespace": "custom",
                                "key": "details",
                                "value": '{"material":"ceramic"}',
                                "type": "json",
                            },
                        ]
                    },
                    "review_changes": [
                        {
                            "scope": "metafield",
                            "subject": "extractor.inferred_option_candidates",
                            "field": "value",
                            "disposition": "applicable",
                            "editable": True,
                        },
                        {
                            "scope": "metafield",
                            "subject": "extractor.inferred_option_candidates",
                            "field": "type",
                            "disposition": "applicable",
                        },
                        {
                            "scope": "metafield",
                            "subject": "custom.details",
                            "field": "value",
                            "disposition": "applicable",
                        },
                        {
                            "scope": "metafield",
                            "subject": "custom.details",
                            "field": "type",
                            "disposition": "applicable",
                        },
                    ],
                }
            ],
        }
    )

    extractor_value, extractor_type, merchant_value, merchant_type = (
        plan["items"][0]["review_changes"]
    )
    assert (extractor_value["presentation"], extractor_value["editable"]) == (
        "technical",
        False,
    )
    assert (extractor_type["presentation"], extractor_type["editable"]) == (
        "technical",
        False,
    )
    assert (merchant_value["presentation"], merchant_value["editable"]) == (
        "merchant",
        True,
    )
    assert (merchant_type["presentation"], merchant_type["editable"]) == (
        "technical",
        False,
    )


def test_build_review_plan_item_is_pure_and_preserves_prior_optional_inclusion():
    source = {"title": "New title", "vendor": "Optional vendor"}
    live = {"id": "gid://shopify/Product/1", "title": "Old title", "vendor": "Old"}
    proposed = {"id": "gid://shopify/Product/1", **source}
    first = build_review_plan_item(
        item_id="item-1",
        currency_code="CAD",
        source_index=3,
        source_product=source,
        live_snapshot=live,
        proposed=proposed,
        operation="update",
    )
    vendor = next(
        row for row in first["review_changes"] if row["field"] == "vendor"
    )
    vendor["included"] = False

    rebuilt = build_review_plan_item(
        item_id="item-2",
        currency_code="CAD",
        source_index=3,
        source_product=source,
        live_snapshot=live,
        proposed=proposed,
        operation="update",
        prior_item=first,
    )

    assert source == {"title": "New title", "vendor": "Optional vendor"}
    assert live["title"] == "Old title"
    assert proposed["title"] == "New title"
    assert rebuilt["item_id"] == "item-2"
    assert rebuilt["operation"] == "update"
    assert rebuilt["selected"] is True
    assert next(
        row for row in rebuilt["review_changes"] if row["field"] == "vendor"
    )["included"] is False
    assert all(
        row["presentation"] == "merchant" and row["editable"] is True
        for row in rebuilt["review_changes"]
    )
