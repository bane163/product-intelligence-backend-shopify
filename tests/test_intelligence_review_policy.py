from application.use_cases.intelligence_review_suggestions import classify_field


def test_review_preview_never_selects_fields_by_default():
    routine = classify_field({"is_reversible": True}, "title", changed=False)
    elevated = classify_field({"details": {"requires_review": True}}, "variant_operations", changed=False)

    assert routine["selected_by_default"] is False
    assert elevated["selected_by_default"] is False
