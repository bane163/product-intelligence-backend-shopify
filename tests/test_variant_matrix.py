import pytest

from application.domain.variant_matrix import build_variant_matrix, validate_variant_matrix


def test_builds_complete_two_dimension_matrix_with_unique_skus_and_copied_price():
    matrix = build_variant_matrix(
        dimensions=[
            {"dimension": "Size", "canonical_values": ["S", "M", "L"]},
            {"dimension": "Color", "canonical_values": ["Red", "Blue"]},
        ],
        sku_prefix="TEE", price="19.95",
    )
    assert len(matrix["create_variants"]) == 6
    assert len({row["sku"] for row in matrix["create_variants"]}) == 6
    assert {row["price"] for row in matrix["create_variants"]} == {"19.95"}
    assert all("inventory_quantity" not in row for row in matrix["create_variants"])


def test_rejects_incomplete_duplicate_and_inventory_override():
    raw = {
        "create_options": [{"name": "Size", "values": ["S", "M"]}],
        "create_variants": [
            {"option_values": [{"option_name": "Size", "name": "S"}], "sku": "X", "price": "1"},
            {"option_values": [{"option_name": "Size", "name": "S"}], "sku": "X", "price": "1", "inventory_quantity": 2},
        ],
    }
    _, errors = validate_variant_matrix(raw)
    assert any("complete Cartesian" in error for error in errors)
    assert any("SKUs must be unique" in error for error in errors)
    assert any("cannot override" in error for error in errors)


def test_catalog_health_cap_is_100():
    with pytest.raises(ValueError, match="limit is 100"):
        build_variant_matrix(
            dimensions=[{"dimension": "A", "canonical_values": [str(i) for i in range(11)]}, {"dimension": "B", "canonical_values": [str(i) for i in range(10)]}],
            sku_prefix="X", price="1",
        )
