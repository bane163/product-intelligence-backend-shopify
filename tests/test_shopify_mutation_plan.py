from application.domain.shopify_mutation_plan import (
    applicable_changes,
    canonical_product,
    merge_tags,
    merge_structured_product,
    preserve_blank_fields,
    validate_product,
)


def test_invalid_status_is_blocked_before_transport():
    blockers, _ = validate_product(
        {"title": "Hat", "status": "published"}, creating=True
    )
    assert "INVALID_STATUS" in {item["code"] for item in blockers}


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
