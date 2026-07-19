from application.domain.shopify_product_normalization import (
    build_product_payload,
    build_product_set_identifier,
    build_product_set_input,
    normalize_product_options,
    normalize_variant_inputs,
)


def test_build_product_payload_preserves_explicit_clear_values() -> None:
    payload = build_product_payload(
        {
            "id": "gid://shopify/Product/1",
            "title": "Catalog Product",
            "vendor": "",
            "body_html": "",
            "product_type": "",
            "tags": [],
            "seo_title": "",
            "seo_description": "",
        },
        include_id=True,
    )

    assert payload["id"] == "gid://shopify/Product/1"
    assert payload["title"] == "Catalog Product"
    assert payload["vendor"] == ""
    assert payload["descriptionHtml"] == ""
    assert payload["productType"] == ""
    assert payload["tags"] == []
    assert payload["seo"] == {"title": "", "description": ""}


def test_build_product_set_input_omits_empty_seo_and_normalizes_tags() -> None:
    payload = build_product_set_input(
        {
            "title": "Catalog Product",
            "body_html": "<p>Desc</p>",
            "tags": "summer, sale",
            "seo_title": "",
            "seo_description": "",
        }
    )

    assert payload["title"] == "Catalog Product"
    assert payload["descriptionHtml"] == "<p>Desc</p>"
    assert payload["tags"] == ["summer", "sale"]
    assert "seo" not in payload


def test_build_product_set_input_serializes_canonical_preflight_fields() -> None:
    payload = build_product_set_input(
        {
            "descriptionHtml": "<p>Canonical description</p>",
            "productType": "Accessories",
            "seo": {"title": "Canonical title", "description": "Canonical SEO"},
        }
    )

    assert payload == {
        "descriptionHtml": "<p>Canonical description</p>",
        "productType": "Accessories",
        "seo": {"title": "Canonical title", "description": "Canonical SEO"},
    }


def test_build_product_set_input_serializes_complete_structured_state_without_inventory_quantities() -> (
    None
):
    payload = build_product_set_input(
        {
            "title": "Trail Tee",
            "productOptions": [
                {
                    "name": "Size",
                    "position": 1,
                    "values": [{"name": "S"}, {"name": "M"}],
                }
            ],
            "variants": [
                {
                    "id": "gid://shopify/ProductVariant/1",
                    "optionValues": [{"optionName": "Size", "name": "S"}],
                    "sku": "TEE-S",
                    "price": "20",
                    "compareAtPrice": "25",
                    "taxable": True,
                    "inventoryPolicy": "DENY",
                    "inventoryQuantities": [{"locationId": "unsafe"}],
                    "inventoryItem": {
                        "cost": "8",
                        "tracked": True,
                        "requiresShipping": True,
                    },
                }
            ],
            "metafields": [
                {
                    "namespace": "custom",
                    "key": "fit",
                    "value": "regular",
                    "type": "single_line_text_field",
                }
            ],
        }
    )
    assert payload["productOptions"][0]["values"] == [{"name": "S"}, {"name": "M"}]
    assert payload["variants"][0]["id"].endswith("/1")
    assert payload["variants"][0]["inventoryItem"]["cost"] == "8"
    assert "inventoryQuantities" not in payload["variants"][0]
    assert "metafields" not in payload


def test_product_payloads_normalize_shopify_status_enum_case() -> None:
    for status in ("active", "Archived", " DRAFT ", "UNLISTED"):
        expected = status.strip().upper()

        assert (
            build_product_set_input({"title": "Catalog Product", "status": status})[
                "status"
            ]
            == expected
        )
        assert (
            build_product_payload(
                {"title": "Catalog Product", "status": status},
                include_id=False,
            )["status"]
            == expected
        )


def test_build_product_set_identifier_requires_server_resolved_gid() -> None:
    assert build_product_set_identifier({"id": "gid://shopify/Product/9"}) == {
        "id": "gid://shopify/Product/9"
    }
    assert build_product_set_identifier({"handle": "catalog-product"}) is None
    assert build_product_set_identifier({"title": "No Match"}) is None


def test_option_and_variant_normalizers_filter_invalid_entries() -> None:
    options = normalize_product_options(
        [
            {"name": "Size", "values": ["S", "M", ""]},
            {"name": "", "values": ["X"]},
            "invalid",
        ]
    )
    assert options == [{"name": "Size", "values": [{"name": "S"}, {"name": "M"}]}]

    variants = normalize_variant_inputs(
        [
            {
                "option_values": [{"option_name": "Size", "name": "S"}],
                "sku": "SKU-S",
                "price": 9.99,
                "inventory_quantity": 3,
            },
            {"option_values": []},
            "invalid",
        ]
    )
    assert variants == [
        {
            "optionValues": [{"optionName": "Size", "name": "S"}],
            "sku": "SKU-S",
            "price": "9.99",
        }
    ]
    assert "inventoryQuantities" not in variants[0]


def test_normalize_variant_inputs_strips_sku_when_strip_sku_true() -> None:
    variants = normalize_variant_inputs(
        [
            {
                "option_values": [{"option_name": "Size", "name": "S"}],
                "sku": "SKU-S",
                "price": "9.99",
            }
        ],
        strip_sku=True,
    )
    assert variants == [
        {
            "optionValues": [{"optionName": "Size", "name": "S"}],
            "price": "9.99",
        }
    ]


def test_normalize_variant_inputs_retains_sku_when_strip_sku_false() -> None:
    variants = normalize_variant_inputs(
        [
            {
                "option_values": [{"option_name": "Size", "name": "S"}],
                "sku": "SKU-S",
                "price": "9.99",
            }
        ],
        strip_sku=False,
    )
    assert variants == [
        {
            "optionValues": [{"optionName": "Size", "name": "S"}],
            "sku": "SKU-S",
            "price": "9.99",
        }
    ]
