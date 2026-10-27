from application.domain.shopify_mutation_plan import review_changes, _preview_cell


def test_metafield_type_is_preserved_independently_on_each_side_and_in_preview():
    old = {"namespace": "custom", "key": "rating", "type": "single_line_text_field", "value": "4"}
    new = {**old, "type": "number_decimal", "value": "4.50"}
    rows = review_changes({"metafields": {"nodes": [old]}}, {}, {"metafields": [new]})
    row = next(row for row in rows if row["scope"] == "metafield" and row["field"] == "value")
    expected = {"current": {"metafield_type": "single_line_text_field"}, "proposed": {"metafield_type": "number_decimal"}}
    assert row["value_metadata"] == expected
    cell = _preview_cell({}, column="custom.rating", scope="metafield", subject="custom.rating", subject_identity=None, value_path="metafields.custom.rating", current="4", proposed="4.50", row=row)
    assert cell["value_metadata"] == expected
    cell["value_metadata"]["current"]["metafield_type"] = "changed"
    assert row["value_metadata"] == expected
