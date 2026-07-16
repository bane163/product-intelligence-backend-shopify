from infrastructure.adapters.azure_document_layout_adapter import _layout_from_payload


def test_normalizes_rotated_words_lines_and_table_cells_across_pages():
    payload = {"analyzeResult": {"pages": [
        {"pageNumber": 1, "width": 10, "height": 20, "words": [{"content": "Lamp", "polygon": [2, 4, 5, 3, 6, 8, 3, 9]}], "lines": []},
        {"pageNumber": 2, "width": 20, "height": 10, "words": [], "lines": [{"content": "Acme", "polygon": [2, 1, 8, 1, 8, 3, 2, 3]}]},
    ], "tables": [{"cells": [{"content": "12.00", "boundingRegions": [{"pageNumber": 2, "polygon": [10, 2, 14, 2, 14, 4, 10, 4]}]}]}]}}
    layout = _layout_from_payload(payload, "canonical.pdf")
    assert layout.page_count == 2
    assert layout.resolve("p1-word-0").bbox == (0.2, 0.15, 0.6, 0.45)
    assert layout.resolve("p2-table-0-cell-0").bbox == (0.5, 0.2, 0.7, 0.4)
    assert layout.resolve("unknown") is None
