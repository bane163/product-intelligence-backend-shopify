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


def test_build_product_set_identifier_prefers_id_then_handle() -> None:
    assert build_product_set_identifier({"id": "gid://shopify/Product/9"}) == {
        "id": "gid://shopify/Product/9"
    }
    assert build_product_set_identifier({"handle": "catalog-product"}) == {
        "handle": "catalog-product"
    }
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
            "inventoryQuantities": [{"availableQuantity": 3}],
        }
    ]
