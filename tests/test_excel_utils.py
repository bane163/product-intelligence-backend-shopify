import io

from openpyxl import Workbook

from ai.excel_utils import extract_excel_contents


def _workbook_bytes() -> bytes:
    workbook = Workbook()
    first = workbook.active
    first.title = "SheetA"
    first.append(["Title", "Price"])
    first.append(["Alpha", 10])

    second = workbook.create_sheet("SheetB")
    second.append(["Title", "Price"])
    second.append(["Beta", 20])

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_extract_excel_contents_includes_all_sheets():
    extracted = extract_excel_contents(
        _workbook_bytes(), max_rows_per_sheet=10, max_sheets=10
    )

    assert "=== Sheet: SheetA ===" in extracted
    assert "=== Sheet: SheetB ===" in extracted
    assert "Alpha\t10" in extracted
    assert "Beta\t20" in extracted
    assert "[CELL_REFS]\tA2=Alpha\tB2=10" in extracted
    assert "[CELL_REFS]\tA2=Beta\tB2=20" in extracted


def test_extract_excel_contents_can_limit_to_first_sheet():
    extracted = extract_excel_contents(
        _workbook_bytes(), max_rows_per_sheet=10, max_sheets=1
    )

    assert "=== Sheet: SheetA ===" in extracted
    assert "=== Sheet: SheetB ===" not in extracted
