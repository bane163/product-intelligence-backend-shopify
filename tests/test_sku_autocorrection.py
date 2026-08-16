from copy import deepcopy

from application.domain.sku_autocorrection import (
    carry_forward_required_live_values,
    generate_missing_skus,
)


def _product(variants, *, options=None):
    return {
        "title": "Tee",
        "options": options or [{"name": "Color", "values": ["Black", "White"]}],
        "variants": variants,
    }


def _variant(value, sku="", price="10", *, size=None):
    values = [{"option_name": "Color", "name": value}]
    if size:
        values.append({"option_name": "Size", "name": size})
    return {"option_values": values, "sku": sku, "price": price}


def test_generates_from_source_sibling_full_value_pattern():
    product = _product([_variant("Black", "TEE-BLACK"), _variant("White")])
    assert generate_missing_skus(product) == {1: "TEE-WHITE"}


def test_generates_from_matched_live_sibling_pattern():
    product = _product([_variant("Blue")])
    live = _product([_variant("Black", "TEE-BLACK"), _variant("White", "TEE-WHITE")])
    assert generate_missing_skus(product, live) == {0: "TEE-BLUE"}


def test_reuses_observed_abbreviation_but_does_not_invent_one():
    options = [{"name": "Color", "values": ["Black", "White"]}]
    with_observation = _product(
        [_variant("Black", "TEE-BLK"), _variant("Black")], options=options
    )
    unseen = _product(
        [_variant("Black", "TEE-BLK"), _variant("White")], options=options
    )
    assert generate_missing_skus(with_observation) == {1: "TEE-BLK"}
    assert generate_missing_skus(unseen) == {}


def test_preserves_multi_option_order_and_casing():
    options = [
        {"name": "Color", "values": ["Red", "Blue"]},
        {"name": "Size", "values": ["Small", "Large"]},
    ]
    product = _product(
        [
            _variant("Red", "SHIRT-RED-SMALL", size="Small"),
            _variant("Blue", size="Large"),
        ],
        options=options,
    )
    assert generate_missing_skus(product) == {1: "SHIRT-BLUE-LARGE"}


def test_rejects_mixed_prefix_separator_and_unparseable_patterns():
    mixed_prefix = _product(
        [
            _variant("Black", "TEE-BLACK"),
            _variant("White", "TOP-WHITE"),
            _variant("Blue"),
        ]
    )
    mixed_separator = _product(
        [
            _variant("Black", "TEE-BLACK"),
            _variant("White", "TEE_WHITE"),
            _variant("Blue"),
        ]
    )
    no_pattern = _product([_variant("Black", "SKU001"), _variant("White")])
    assert generate_missing_skus(mixed_prefix) == {}
    assert generate_missing_skus(mixed_separator) == {}
    assert generate_missing_skus(no_pattern) == {}


def test_live_carry_forward_only_changes_blank_matched_required_values():
    product = _product([_variant("Black", "", ""), _variant("White", "NEW", "bad")])
    product["title"] = ""
    live = _product(
        [_variant("Black", "LIVE-BLACK", "19"), _variant("White", "LIVE-WHITE", "20")]
    )
    live["title"] = "Live title"
    result = deepcopy(product)
    carry_forward_required_live_values(result, live)
    assert result["title"] == "Live title"
    assert result["variants"][0]["sku"] == "LIVE-BLACK"
    assert result["variants"][0]["price"] == "19"
    assert result["variants"][1]["sku"] == "NEW"
    assert result["variants"][1]["price"] == "bad"
