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
