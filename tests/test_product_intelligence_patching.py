from application.domain.product_intelligence_patching import apply_suggestions_to_products


def test_apply_suggestions_to_products_applies_supported_fields_and_operations():
    products = [
        {
            "title": "Demo",
            "tags": ["legacy"],
            "metafields": [
                {
                    "namespace": "extractor",
                    "key": "source_confidence",
                    "type": "single_line_text_field",
                    "value": "low",
                }
            ],
        }
    ]
    suggestions = [
        {
            "product_index": 0,
            "patch_payload": {
                "vendor": "Enriched Vendor",
                "tags": "alpha, beta",
                "metafields": [
                    {
                        "namespace": "extractor",
                        "key": "source_confidence",
                        "type": "single_line_text_field",
                        "value": "high",
                    },
                    {
                        "namespace": "extractor",
                        "key": "origin",
                        "type": "single_line_text_field",
                        "value": "llm",
                    },
                ],
                "variant_operations": {
                    "create_options": [{"name": "Size", "values": ["S", "M"]}],
                    "create_variants": [
                        {
                            "option_values": [{"option_name": "Size", "name": "S"}],
                            "sku": "DEMO-S",
                        }
                    ],
                },
            },
        }
    ]

    enhanced, operations_by_index = apply_suggestions_to_products(
        products=products, suggestions=suggestions
    )

    assert enhanced[0]["vendor"] == "Enriched Vendor"
    assert enhanced[0]["tags"] == "alpha, beta"
    assert enhanced[0]["metafields"] == [
        {
            "namespace": "extractor",
            "key": "source_confidence",
            "type": "single_line_text_field",
            "value": "high",
        },
        {
            "namespace": "extractor",
            "key": "origin",
            "type": "single_line_text_field",
            "value": "llm",
        },
    ]
    assert 0 in operations_by_index
    assert len(operations_by_index[0]) == 1
    assert operations_by_index[0][0]["create_options"][0]["name"] == "Size"
    assert operations_by_index[0][0]["create_variants"][0]["sku"] == "DEMO-S"


def test_apply_suggestions_records_exact_origins_for_all_review_value_paths():
    products = [{"title": "Demo"}]
    suggestions = [
        {
            "product_index": 0,
            "value_origin": "ai_assisted",
            "patch_payload": {
                "body_html": "AI description",
                "metafields": [
                    {
                        "namespace": " custom ",
                        "key": "details",
                        "type": "single_line_text_field",
                        "value": "AI material",
                    }
                ],
                "variant_operations": {
                    "create_options": [
                        {"name": " Size ", "values": ["Small"]}
                    ],
                    "create_variants": [
                        {
                            "option_values": [
                                {"option_name": " Size ", "name": " Small "}
                            ],
                            "sku": "DEMO-S",
                            "price": "12.00",
                            "inventory_quantity": 4,
                        }
                    ],
                },
            },
        }
    ]

    enhanced, _ = apply_suggestions_to_products(
        products=products, suggestions=suggestions
    )

    assert enhanced[0]["value_origins"] == {
        "descriptionHtml": "ai_assisted",
        "metafield[custom-details].value": "ai_assisted",
        "metafield[custom-details].type": "ai_assisted",
        "option[size].values": "ai_assisted",
        "variant[size-small].sku": "ai_assisted",
        "variant[size-small].price": "ai_assisted",
        "inventory[size-small].inventory_quantity": "ai_assisted",
    }


def test_later_suggestion_updates_exact_origin_without_touching_unpatched_values():
    products = [
        {
            "title": "Demo",
            "value_origins": {
                "title": "source",
                "metafield[custom-details].value": "source",
            },
        }
    ]
    suggestions = [
        {
            "product_index": 0,
            "value_origin": "derived",
            "patch_payload": {
                "metafields": [
                    {
                        "namespace": "custom",
                        "key": "details",
                        "value": "Normalized",
                    }
                ]
            },
        }
    ]

    enhanced, _ = apply_suggestions_to_products(
        products=products, suggestions=suggestions
    )

    assert enhanced[0]["value_origins"] == {
        "title": "source",
        "metafield[custom-details].value": "derived",
    }


def test_apply_suggestions_to_products_ignores_invalid_payloads():
    products = [{"title": "Demo"}]
    suggestions = [
        None,
        {"product_index": "nope", "patch_payload": {"vendor": "Ignored"}},
        {"product_index": 99, "patch_payload": {"vendor": "Ignored"}},
        {"product_index": 0, "patch_payload": {}},
        {"product_index": 0, "patch_payload": "invalid"},
    ]

    enhanced, operations_by_index = apply_suggestions_to_products(
        products=products, suggestions=suggestions
    )

    assert enhanced == products
    assert operations_by_index == {}


def test_apply_suggestions_to_products_does_not_apply_unknown_fields():
    products = [{"title": "Demo"}]
    suggestions = [
        {
            "product_index": 0,
            "patch_payload": {
                "title": "Renamed Demo",
                "unknown_field": "should-not-apply",
            },
        }
    ]

    enhanced, _ = apply_suggestions_to_products(products=products, suggestions=suggestions)

    assert enhanced[0]["title"] == "Renamed Demo"
    assert "unknown_field" not in enhanced[0]


def test_no_op_ai_patch_preserves_source_origins_and_source_refs():
    source_refs = [
        {"value_path": "title", "page": 1},
        {"value_path": "descriptionHtml", "page": 1},
        {"value_path": "variant[title-default-title].sku", "page": 1},
    ]
    products = [{
        "title": "Studio Mixer",
        "descriptionHtml": "<p>Compact mixer</p>",
        "vendor": "Sound Co",
        "productType": "Electronics",
        "tags": ["Audio", "Studio"],
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [{
            "option1": "Default Title", "sku": "SMX-100", "price": "89.99"
        }],
        "metafields": [{
            "namespace": "custom", "key": "material",
            "type": "single_line_text_field", "value": "Metal",
        }],
        "value_origins": {
            "title": "source",
            "descriptionHtml": "source",
            "vendor": "source",
            "productType": "source",
            "tags": "source",
            "metafield[custom-material].value": "source",
            "metafield[custom-material].type": "source",
            "option[title].values": "source",
            "variant[title-default-title].sku": "source",
            "variant[title-default-title].price": "source",
        },
        "source_refs": source_refs,
    }]
    suggestions = [{
        "product_index": 0,
        "value_origin": "ai_assisted",
        "patch_payload": {
            "title": "Studio Mixer",
            "body_html": "<p>Compact mixer</p>",
            "vendor": "Sound Co",
            "product_type": "Electronics",
            "tags": "studio, audio",
            "metafields": [{
                "namespace": "custom", "key": "material",
                "type": "single_line_text_field", "value": "Metal",
            }],
            "variant_operations": {
                "create_options": [{"name": "Title", "values": ["Default Title"]}],
                "create_variants": [{
                    "option_values": [{"option_name": "Title", "name": "Default Title"}],
                    "sku": "SMX-100", "price": 89.99,
                }],
            },
        },
    }]

    enhanced, _ = apply_suggestions_to_products(products=products, suggestions=suggestions)

    assert set(enhanced[0]["value_origins"].values()) == {"source"}
    assert enhanced[0]["source_refs"] is source_refs
    assert products[0]["value_origins"] == enhanced[0]["value_origins"]


def test_mixed_ai_patch_changes_only_meaningfully_different_origin_paths():
    products = [{
        "title": "Mixer",
        "vendor": "Sound Co",
        "tags": ["Audio", "Studio"],
        "metafields": [{
            "namespace": "custom", "key": "details",
            "type": "single_line_text_field", "value": "Metal",
        }],
        "value_origins": {
            "title": "source", "vendor": "source", "tags": "source",
            "metafield[custom-details].value": "source",
            "metafield[custom-details].type": "source",
        },
    }]
    suggestions = [{
        "product_index": 0,
        "value_origin": "ai_assisted",
        "patch_payload": {
            "title": "Mixer",
            "vendor": "New Sound Co",
            "tags": "studio, audio",
            "metafields": [{
                "namespace": "custom", "key": "details",
                "type": "multi_line_text_field", "value": "Metal",
            }],
        },
    }]

    enhanced, _ = apply_suggestions_to_products(products=products, suggestions=suggestions)

    assert enhanced[0]["value_origins"] == {
        "title": "source",
        "vendor": "ai_assisted",
        "tags": "source",
        "metafield[custom-details].value": "source",
        "metafield[custom-details].type": "ai_assisted",
    }
