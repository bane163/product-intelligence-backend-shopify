"""Shared durable review-history semantics."""

CONTENT_HISTORY_OPERATIONS = frozenset(
    {
        "value_edit",
        "value_revert",
        "cell_edit",
        "cell_revert",
        "option_values_edit",
        "selection",
        "option_dimension_add",
        "option_dimension_rename",
        "option_dimension_reorder",
        "option_dimension_remove",
        "variant_add",
        "variant_remove",
        "media_gallery_update",
    }
)
