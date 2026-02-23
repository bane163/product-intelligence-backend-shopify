"""Use-case: create a highlighted spreadsheet copy for source verification."""

from __future__ import annotations

import io
import os
import re
import uuid
from typing import Any

from openpyxl import load_workbook
from openpyxl.styles import PatternFill
from openpyxl.utils import get_column_letter, range_boundaries

from application.ports.supabase_port import SupabasePort
from application.services.document_formats import classify_document

_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_XLSM_CONTENT_TYPE = "application/vnd.ms-excel.sheet.macroenabled.12"
_HIGHLIGHT_FILL = PatternFill(
    fill_type="solid",
    start_color="FFF59D",
    end_color="FFF59D",
)


def _optional_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _normalize_sheet_name(value: str) -> str:
    trimmed = value.strip()
    if trimmed.startswith("'") and trimmed.endswith("'") and len(trimmed) >= 2:
        return trimmed[1:-1].replace("''", "'")
    return trimmed


def _split_sheet_and_ref(value: str) -> tuple[str | None, str]:
    trimmed = value.strip()
    if not trimmed:
        return None, ""

    if "!" in trimmed:
        raw_sheet, raw_ref = trimmed.rsplit("!", 1)
        return _normalize_sheet_name(raw_sheet), raw_ref.strip()

    quoted_dot_match = re.match(r"^('(?:''|[^'])+')\.(.+)$", trimmed)
    if quoted_dot_match:
        return _normalize_sheet_name(quoted_dot_match.group(1)), quoted_dot_match.group(
            2
        ).strip()

    plain_dot_match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\.(.+)$", trimmed)
    if plain_dot_match:
        return plain_dot_match.group(1).strip(), plain_dot_match.group(2).strip()

    return None, trimmed


def _normalize_cell_ref(value: str) -> str:
    normalized = value.replace("$", "").strip().upper()
    match = re.match(r"^([A-Z]+)(\d+)$", normalized)
    if not match:
        raise ValueError(f"Invalid cell reference: {value}")
    row = int(match.group(2))
    if row < 1:
        raise ValueError(f"Invalid row index in cell reference: {value}")
    return f"{match.group(1)}{row}"


def _resolve_target_range(worksheet, target_ref: str) -> str:
    normalized = target_ref.replace("$", "").strip().upper()
    if not normalized:
        raise ValueError("Missing source coordinates")

    max_col = max(worksheet.max_column or 0, 1)

    row_range_match = re.match(r"^(\d+)(?::(\d+))?$", normalized)
    if row_range_match:
        start_row = int(row_range_match.group(1))
        end_row = int(row_range_match.group(2) or row_range_match.group(1))
        if start_row < 1 or end_row < 1:
            raise ValueError(f"Invalid row range: {target_ref}")
        if start_row > end_row:
            start_row, end_row = end_row, start_row
        return f"A{start_row}:{get_column_letter(max_col)}{end_row}"

    if ":" in normalized:
        left_raw, right_raw = normalized.split(":", 1)
        left = _normalize_cell_ref(left_raw)
        right = _normalize_cell_ref(right_raw)
        min_col, min_row, max_col_idx, max_row = range_boundaries(f"{left}:{right}")
        return (
            f"{get_column_letter(min_col)}{min_row}:"
            f"{get_column_letter(max_col_idx)}{max_row}"
        )

    return _normalize_cell_ref(normalized)


def _build_target(
    *,
    workbook,
    raw_sheet: str | None,
    raw_ref: str,
    fallback_sheet: str | None,
    field: str | None = None,
) -> dict[str, str] | None:
    source_sheet, source_ref = _split_sheet_and_ref(raw_ref)
    normalized_sheet = _normalize_sheet_name(
        source_sheet or (raw_sheet or "").strip() or (fallback_sheet or "").strip()
    )
    target_sheet = normalized_sheet or workbook.active.title
    if target_sheet not in workbook.sheetnames:
        return None

    worksheet = workbook[target_sheet]
    target_ref = source_ref or raw_ref
    normalized_target_range = _resolve_target_range(worksheet, target_ref)
    target: dict[str, str] = {
        "sheet": target_sheet,
        "cell_range": normalized_target_range,
    }
    if field:
        target["field"] = field
    return target


def execute(
    *,
    supabase: SupabasePort,
    source_file_id: str,
    sheet: str | None = None,
    cell: str | None = None,
    cell_range: str | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    preferred_sheet: str | None = None,
    highlight_file_id: str | None = None,
) -> dict[str, str]:
    file_entry = supabase.get_file(source_file_id)
    if not file_entry:
        raise LookupError("Source file not found")

    content = file_entry.get("content")
    if not isinstance(content, (bytes, bytearray)):
        raise ValueError("Stored source file content is invalid")
    source_bytes = bytes(content)
    source_filename = _optional_str(file_entry, "name") or f"{source_file_id}.xlsx"
    source_content_type = _optional_str(file_entry, "content_type")

    source_format = classify_document(
        filename=source_filename,
        content_type=source_content_type,
        file_bytes=source_bytes,
    )
    if not source_format.is_spreadsheet:
        raise ValueError("Source highlighting is only available for spreadsheet files")

    coordinate_input = (cell_range or cell or "").strip()
    if not coordinate_input and not source_refs:
        raise ValueError("Missing source coordinate (cell or cell_range)")

    source_sheet: str | None = None
    source_ref = ""
    normalized_sheet = _normalize_sheet_name((sheet or "").strip())
    if coordinate_input:
        source_sheet, source_ref = _split_sheet_and_ref(coordinate_input)
        normalized_sheet = _normalize_sheet_name(source_sheet or (sheet or "").strip())

    keep_vba = source_filename.lower().endswith(".xlsm")
    workbook = load_workbook(io.BytesIO(source_bytes), keep_vba=keep_vba)
    try:
        targets: list[dict[str, str]] = []
        if source_refs:
            for source_ref_entry in source_refs:
                if not isinstance(source_ref_entry, dict):
                    continue
                source_field = source_ref_entry.get("field")
                field = (
                    source_field.lower().strip()
                    if isinstance(source_field, str) and source_field.strip()
                    else None
                )
                source_sheet_hint = source_ref_entry.get("sheet")
                sheet_hint = (
                    str(source_sheet_hint)
                    if isinstance(source_sheet_hint, str) and source_sheet_hint.strip()
                    else None
                )
                source_cell_range = source_ref_entry.get("cell_range")
                source_cell = source_ref_entry.get("cell")
                raw_ref_value = (
                    source_cell_range
                    if isinstance(source_cell_range, str) and source_cell_range.strip()
                    else source_cell
                    if isinstance(source_cell, str) and source_cell.strip()
                    else None
                )
                if not raw_ref_value:
                    continue
                target = _build_target(
                    workbook=workbook,
                    raw_sheet=sheet_hint,
                    raw_ref=str(raw_ref_value),
                    fallback_sheet=preferred_sheet or sheet,
                    field=field,
                )
                if target:
                    targets.append(target)

        if not targets:
            target_ref = source_ref or coordinate_input
            fallback_target = _build_target(
                workbook=workbook,
                raw_sheet=normalized_sheet or sheet,
                raw_ref=target_ref,
                fallback_sheet=preferred_sheet,
            )
            if not fallback_target:
                fallback_sheet_name = _normalize_sheet_name(
                    normalized_sheet or preferred_sheet or workbook.active.title
                )
                raise ValueError(
                    f"Sheet '{fallback_sheet_name}' was not found in source workbook"
                )
            targets.append(fallback_target)

        deduped_targets: list[dict[str, str]] = []
        seen_targets: set[tuple[str, str]] = set()
        for target in targets:
            key = (target["sheet"], target["cell_range"])
            if key in seen_targets:
                continue
            seen_targets.add(key)
            deduped_targets.append(target)
        targets = deduped_targets

        for target in targets:
            worksheet = workbook[target["sheet"]]
            normalized_target_range = target["cell_range"]
            min_col, min_row, max_col, max_row = range_boundaries(normalized_target_range)
            for row in worksheet.iter_rows(
                min_row=min_row,
                max_row=max_row,
                min_col=min_col,
                max_col=max_col,
            ):
                for current_cell in row:
                    current_cell.fill = _HIGHLIGHT_FILL

        title_target = next((item for item in targets if item.get("field") == "title"), None)
        preferred_sheet_name = _normalize_sheet_name(preferred_sheet or "")
        preferred_target = (
            next((item for item in targets if item["sheet"] == preferred_sheet_name), None)
            if preferred_sheet_name
            else None
        )
        selected_target = title_target or preferred_target or targets[0]
        target_sheet = selected_target["sheet"]
        normalized_target_range = selected_target["cell_range"]
        worksheet = workbook[target_sheet]
        min_col, min_row, _, _ = range_boundaries(normalized_target_range)
        top_left = f"{get_column_letter(min_col)}{min_row}"
        if worksheet.sheet_view.selection:
            worksheet.sheet_view.selection[0].activeCell = top_left
            worksheet.sheet_view.selection[0].sqref = normalized_target_range
        workbook.active = workbook.sheetnames.index(target_sheet)

        output = io.BytesIO()
        workbook.save(output)
    finally:
        workbook.close()

    output_file_id = (highlight_file_id or "").strip() or str(uuid.uuid4())
    base_name, _ = os.path.splitext(source_filename)
    output_ext = ".xlsm" if keep_vba else ".xlsx"
    output_filename = f"{base_name or 'document'}-source-highlight{output_ext}"
    output_content_type = _XLSM_CONTENT_TYPE if keep_vba else _XLSX_CONTENT_TYPE

    supabase.save_file(
        file_id=output_file_id,
        name=output_filename,
        content=output.getvalue(),
        content_type=output_content_type,
    )

    return {
        "file_id": output_file_id,
        "filename": output_filename,
        "sheet": target_sheet,
        "cell_range": normalized_target_range,
    }
