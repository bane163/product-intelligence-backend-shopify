"""Conservatively add canonical, type-aware spreadsheet source evidence."""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Iterable

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, range_boundaries

from application.domain.review_value_paths import (
    ReviewValue,
    canonical_value_path,
    is_numeric_field,
    iter_review_values,
    values_equal,
)
from application.domain.field_derivation_policy import (
    NON_SOURCE_ORIGINS,
    is_protected_field,
    remove_unverified_protected_values,
)


_HEADER_ALIASES = {
    "handle": {"handle"},
    "title": {"title", "product title", "product name", "name"},
    "descriptionHtml": {"body html", "body (html)", "description html", "description", "body"},
    "vendor": {"vendor", "brand", "manufacturer", "supplier"},
    "productType": {"type", "product type", "product category"},
    "status": {"status"},
    "published": {"published"},
    "sku": {"sku", "variant sku", "product code", "item code"},
    "price": {"price", "variant price", "unit price", "retail price"},
    "compareAtPrice": {"compare at price", "compareatprice", "variant compare at price", "msrp"},
    "barcode": {"barcode", "variant barcode"},
    "taxable": {"taxable", "is taxable"},
    "taxCode": {"tax code", "variant tax code"},
    "tags": {"tags", "product tags"},
    "values": {"values", "option values"},
    "value": {"value", "metafield value"},
    "type": {"type", "metafield type"},
    "inventoryItem.cost": {"cost", "unit cost", "cost per item"},
    "inventoryItem.tracked": {"tracked", "track inventory", "inventory tracked", "variant inventory tracker"},
    "inventoryItem.requiresShipping": {"requires shipping", "shipping required", "variant requires shipping"},
    "inventoryItem.weight.value": {"weight", "variant weight", "variant grams"},
    "inventoryItem.weight.unit": {"weight unit", "variant weight unit"},
    "inventory_tracker": {"variant inventory tracker", "inventory tracker"},
    "inventory_quantity": {"inventory quantity", "variant inventory qty", "variant inventory quantity"},
    "inventoryPolicy": {"inventory policy", "variant inventory policy"},
    "fulfillment_service": {"fulfillment service", "variant fulfillment service"},
    "grams": {"variant grams", "grams"},
    "variant_image": {"variant image"},
    "variant_tax_code": {"variant tax code", "tax code"},
    "price_international": {"price international", "price / international"},
    "compare_at_price_international": {
        "compare at price international", "compare at price / international",
    },
    "image_src": {"image src", "image source"},
    "image_position": {"image position"},
    "image_alt_text": {"image alt text", "image alt"},
    "gift_card": {"gift card"},
    "seo.title": {"seo title"},
    "seo.description": {"seo description"},
    "internationalPrices": {"price international", "price / international"},
    "internationalCompareAtPrices": {
        "compare at price international", "compare at price / international",
    },
    "google_shopping_category": {"google shopping / google product category"},
    "google_shopping_gender": {"google shopping / gender"},
    "google_shopping_age_group": {"google shopping / age group"},
    "google_shopping_mpn": {"google shopping / mpn"},
    "google_adwords_grouping": {"google shopping / adwords grouping"},
    "google_adwords_labels": {"google shopping / adwords labels"},
    "google_shopping_condition": {"google shopping / condition"},
    "google_shopping_custom_product": {"google shopping / custom product"},
    "google_custom_label_0": {"google shopping / custom label 0"},
    "google_custom_label_1": {"google shopping / custom label 1"},
    "google_custom_label_2": {"google shopping / custom label 2"},
    "google_custom_label_3": {"google shopping / custom label 3"},
    "google_custom_label_4": {"google shopping / custom label 4"},
}


def _header(value: Any) -> str:
    return re.sub(r"[_\s-]+", " ", str(value or "").strip()).casefold()


def _split_qualified_ref(value: Any) -> tuple[str | None, str]:
    raw = str(value or "").strip()
    if "!" not in raw:
        return None, raw
    sheet, coordinate = raw.rsplit("!", 1)
    sheet = sheet.strip()
    if len(sheet) >= 2 and sheet[0] == sheet[-1] == "'":
        sheet = sheet[1:-1].replace("''", "'")
    return sheet, coordinate.strip()


def _ref_location(ref: dict[str, Any]) -> tuple[str | None, str]:
    qualified_sheet, coordinate = _split_qualified_ref(ref.get("cell") or ref.get("cell_range") or "")
    declared = str(ref.get("sheet") or "").strip() or None
    if qualified_sheet and declared and qualified_sheet != declared:
        return None, ""
    return qualified_sheet or declared, coordinate


def _rows(file_bytes: bytes, filename: str | None) -> Iterable[tuple[str, list[list[Any]]]]:
    if (filename or "").casefold().endswith(".csv"):
        yield "Sheet1", [list(row) for row in csv.reader(io.StringIO(file_bytes.decode("utf-8-sig")))]
        return
    workbook = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            yield sheet.title, [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def _bounds(coordinate: str) -> tuple[int, int, int, int] | None:
    try:
        return range_boundaries(coordinate)
    except (TypeError, ValueError):
        return None


def _cells(rows: list[list[Any]], coordinate: str) -> list[tuple[int, int, Any]]:
    bounds = _bounds(coordinate)
    if not bounds:
        return []
    min_col, min_row, max_col, max_row = bounds
    result = []
    for row_no in range(min_row, max_row + 1):
        for col_no in range(min_col, max_col + 1):
            value = rows[row_no - 1][col_no - 1] if row_no <= len(rows) and col_no <= len(rows[row_no - 1]) else None
            result.append((row_no, col_no, value))
    return result


def _region_match(rows: list[list[Any]], coordinate: str, item: ReviewValue) -> tuple[str, str] | None:
    cells = _cells(rows, coordinate)
    direct = [
        (row, col) for row, col, value in cells
        if values_equal(value, item.value, numeric_strings=is_numeric_field(item.field))
    ]
    if len(direct) == 1:
        row, col = direct[0]
        return "cell", f"{get_column_letter(col)}{row}"
    if direct:
        return None
    nonempty = [value for _, _, value in cells if value is not None and value != ""]
    if isinstance(item.value, list) and values_equal(nonempty, item.value):
        return "cell_range", coordinate
    return None


def _parent_region_match(
    rows: list[list[Any]], coordinate: str, item: ReviewValue, product: dict[str, Any]
) -> tuple[str, str] | None:
    """Promote a structured parent cell only after exact path membership checks."""
    matches: list[tuple[int, int]] = []
    for row, col, raw in _cells(rows, coordinate):
        if not isinstance(raw, str):
            continue
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            continue
        values = parsed if isinstance(parsed, list) else [parsed]
        container: dict[str, Any] = {}
        if item.scope == "option":
            container["options"] = values
        elif item.scope == "variant":
            container["options"] = product.get("options") or product.get("productOptions") or []
            container["variants"] = values
        elif item.scope == "metafield":
            container["metafields"] = values
        else:
            continue
        members = [
            candidate for candidate in iter_review_values(container)
            if canonical_value_path(candidate.value_path) == canonical_value_path(item.value_path)
            and values_equal(candidate.value, item.value, numeric_strings=is_numeric_field(item.field))
        ]
        if len(members) == 1:
            matches.append((row, col))
    if len(matches) == 1:
        row, col = matches[0]
        return "cell", f"{get_column_letter(col)}{row}"
    # An option values range is itself the ordered list and needs no guessed child cell.
    if item.scope == "option":
        return _region_match(rows, coordinate, item)
    return None


def _ref_path(ref: dict[str, Any]) -> str:
    return canonical_value_path(ref.get("value_path") or ref.get("field"))


def _parent_scope(ref: dict[str, Any]) -> str | None:
    field = str(ref.get("field") or "").strip().casefold()
    return {"options": "option", "option": "option", "variants": "variant", "variant": "variant", "metafields": "metafield", "metafield": "metafield"}.get(field)


def _parent_evidence_roles(item: ReviewValue) -> tuple[str, ...]:
    """A structured cell can exactly contribute both its identity and value."""
    if item.scope == "option":
        return ("label", "value")
    if item.scope == "metafield":
        return ("key", "label", "value") if item.field == "type" else ("key", "value")
    return ("value",)


def _origin_for(product: dict[str, Any], path: str) -> str | None:
    origins = product.get("value_origins") or product.get("_value_origins")
    if not isinstance(origins, dict):
        return None
    matches = {str(origin) for key, origin in origins.items() if canonical_value_path(key) == canonical_value_path(path)}
    return next(iter(matches)) if len(matches) == 1 else None


def _structured_signature(path: Any) -> tuple[str, str] | None:
    match = re.fullmatch(r"([^\[\]]+)\[.*\]\.(.+)", canonical_value_path(path))
    return (match.group(1), match.group(2)) if match else None


def _reconciled_structured_refs(
    refs: list[dict[str, Any]],
    items: list[ReviewValue],
    sheets: dict[str, list[list[Any]]],
    allowed_sheets: set[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Resolve one legacy structured path only when location and value are unique."""
    exact_paths = {canonical_value_path(item.value_path) for item in items}
    matches_by_item: dict[str, list[dict[str, Any]]] = {}
    attempted_by_item: dict[str, list[dict[str, Any]]] = {}
    items_by_ref: dict[int, list[str]] = {}
    for index, ref in enumerate(refs):
        path = canonical_value_path(ref.get("value_path"))
        signature = _structured_signature(path)
        if not signature or path in exact_paths:
            continue
        sheet, coordinate = _ref_location(ref)
        for item in items:
            if _structured_signature(item.value_path) != signature:
                continue
            if canonical_value_path(ref.get("field")) not in {
                "", canonical_value_path(item.field)
            }:
                continue
            attempted_by_item.setdefault(item.value_path, []).append(ref)
            if (
                sheet not in allowed_sheets
                or sheet not in sheets
                or not _region_match(sheets[sheet], coordinate, item)
            ):
                continue
            matches_by_item.setdefault(item.value_path, []).append(ref)
            items_by_ref.setdefault(index, []).append(item.value_path)

    reconciled: dict[str, dict[str, Any]] = {}
    for item_path, matches in matches_by_item.items():
        if len(matches) != 1:
            continue
        ref_index = next(
            index for index, candidate in enumerate(refs) if candidate is matches[0]
        )
        if len(items_by_ref.get(ref_index, [])) == 1:
            reconciled[item_path] = matches[0]
    return reconciled, attempted_by_item


def _columns(row: list[Any], review_values: list[ReviewValue]) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for index, raw in enumerate(row):
        normalized = _header(raw)
        for field, aliases in _HEADER_ALIASES.items():
            if normalized in {_header(alias) for alias in aliases}:
                result.setdefault(field, []).append(index)
        for item in review_values:
            dynamic = {
                _header(item.field), _header(item.value_path),
                _header(f"{item.subject} {item.field}"),
            }
            if item.scope == "option":
                dynamic.add(_header(item.subject))
            if normalized and normalized in dynamic:
                result.setdefault(item.value_path, []).append(index)
                result.setdefault(item.field, []).append(index)
    return result


def _field_columns(columns: dict[str, list[int]], item: ReviewValue) -> list[int]:
    return list(dict.fromkeys(columns.get(item.value_path, []) + columns.get(item.field, [])))


def _make_ref(
    item: ReviewValue,
    sheet: str,
    kind: str,
    coordinate: str,
    source_file_id: str | None,
    value: Any,
    evidence_role: str = "value",
) -> dict[str, Any]:
    ref = {
        "field": item.field, "value_path": item.value_path,
        "document_kind": "spreadsheet", "source_file_id": source_file_id,
        "sheet": sheet, kind: coordinate, "value": str(value),
        "evidence_role": evidence_role,
    }
    return ref


def _single_column(columns: dict[str, list[int]], field: str) -> int | None:
    values = list(dict.fromkeys(columns.get(field, [])))
    return values[0] if len(values) == 1 else None


def _option_columns(header: list[Any]) -> dict[int, dict[str, int]]:
    result: dict[int, dict[str, int]] = {}
    for index, raw in enumerate(header):
        match = re.fullmatch(r"option\s*([1-3])\s*(name|value)", _header(raw))
        if match:
            result.setdefault(int(match.group(1)), {})[match.group(2)] = index
    return result


def _owned_rows(
    product: dict[str, Any],
    rows: list[list[Any]],
    header_index: int,
    columns: dict[str, list[int]],
) -> list[int]:
    """Resolve rows for one product without borrowing a neighbour's cells."""
    handle_col = _single_column(columns, "handle")
    handle = product.get("handle")
    if handle_col is not None and handle not in (None, ""):
        matches = [
            index for index in range(header_index + 1, len(rows))
            if handle_col < len(rows[index])
            and values_equal(rows[index][handle_col], handle)
        ]
        if matches:
            return matches

    title_col = _single_column(columns, "title")
    title = product.get("title")
    if title_col is None or title in (None, ""):
        return []
    owned: list[int] = []
    active = False
    for index in range(header_index + 1, len(rows)):
        raw = rows[index][title_col] if title_col < len(rows[index]) else None
        if raw not in (None, ""):
            active = values_equal(raw, title)
        if active:
            owned.append(index)
    return owned


def _variant_for_item(
    product: dict[str, Any], item: ReviewValue
) -> dict[str, Any] | None:
    if item.scope not in {"variant", "inventory"}:
        return None
    options = product.get("productOptions") if isinstance(product.get("productOptions"), list) else product.get("options")
    option_names = [
        str(option.get("name") or "").strip()
        for option in options if isinstance(option, dict) and option.get("name")
    ] if isinstance(options, list) else []
    variants = product.get("variants")
    if isinstance(variants, dict):
        variants = variants.get("nodes")
    matches = []
    for variant in variants if isinstance(variants, list) else []:
        if not isinstance(variant, dict):
            continue
        pairs = []
        raw_pairs = variant.get("selectedOptions") or variant.get("selected_options") or variant.get("optionValues") or variant.get("option_values")
        if isinstance(raw_pairs, list):
            for index, pair in enumerate(raw_pairs):
                if not isinstance(pair, dict):
                    continue
                name = pair.get("optionName") or pair.get("option_name")
                value = pair.get("value")
                if value is None and pair.get("name") is not None:
                    if name:
                        value = pair.get("name")
                    else:
                        name = option_names[index] if index < len(option_names) else None
                        value = pair.get("name")
                if name is not None and value is not None:
                    pairs.append((str(name).strip(), str(value).strip()))
        if not pairs:
            pairs = [
                (name, str(variant.get(f"option{index}") or "").strip())
                for index, name in enumerate(option_names, 1)
                if variant.get(f"option{index}") not in (None, "")
            ]
        subject = " / ".join(f"{name}: {value}" for name, value in pairs)
        if subject == item.subject:
            matches.append(variant)
    if not matches and isinstance(variants, list) and len(variants) == 1:
        return variants[0]
    return matches[0] if len(matches) == 1 else None


def _variant_row_indices(
    variant: dict[str, Any],
    rows: list[list[Any]],
    owned: list[int],
    columns: dict[str, list[int]],
    option_columns: dict[int, dict[str, int]],
) -> list[int]:
    sku_col = _single_column(columns, "sku")
    sku = variant.get("sku")
    if sku_col is not None and sku not in (None, ""):
        return [
            index for index in owned
            if sku_col < len(rows[index]) and values_equal(rows[index][sku_col], sku)
        ]

    tuple_values = [
        variant.get(f"option{position}") for position in sorted(option_columns)
        if variant.get(f"option{position}") not in (None, "")
    ]
    if not tuple_values:
        raw_pairs = variant.get("selectedOptions") or variant.get("selected_options") or variant.get("optionValues") or variant.get("option_values")
        if isinstance(raw_pairs, list):
            tuple_values = [
                pair.get("value") if pair.get("value") is not None else pair.get("name")
                for pair in raw_pairs if isinstance(pair, dict)
            ]
    if not tuple_values:
        return []
    matches = []
    for index in owned:
        actual = []
        for position in sorted(option_columns):
            value_col = option_columns[position].get("value")
            if value_col is not None:
                actual.append(rows[index][value_col] if value_col < len(rows[index]) else None)
        if len(actual) >= len(tuple_values) and all(
            values_equal(actual[offset], expected)
            for offset, expected in enumerate(tuple_values)
        ):
            matches.append(index)
    return matches


def _option_refs(
    item: ReviewValue,
    sheet: str,
    rows: list[list[Any]],
    owned: list[int],
    option_columns: dict[int, dict[str, int]],
    source_file_id: str | None,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for mapping in option_columns.values():
        name_col, value_col = mapping.get("name"), mapping.get("value")
        if name_col is None or value_col is None:
            continue
        for row_index in owned:
            label = rows[row_index][name_col] if name_col < len(rows[row_index]) else None
            value = rows[row_index][value_col] if value_col < len(rows[row_index]) else None
            if not values_equal(label, item.subject) or not any(
                values_equal(value, expected) for expected in (item.value if isinstance(item.value, list) else [item.value])
            ):
                continue
            matches.append(_make_ref(
                item, sheet, "cell", f"{get_column_letter(name_col + 1)}{row_index + 1}",
                source_file_id, label, "label",
            ))
            matches.append(_make_ref(
                item, sheet, "cell", f"{get_column_letter(value_col + 1)}{row_index + 1}",
                source_file_id, value, "value",
            ))
    return matches


def enrich_spreadsheet_source_refs(
    products: list[dict[str, Any]], file_bytes: bytes, filename: str | None,
    source_file_id: str | None = None,
) -> None:
    """Replace untrusted refs with uniquely verified canonical cells/ranges."""
    try:
        sheets = {name: rows for name, rows in _rows(file_bytes, filename)}
    except Exception:
        return

    for product in products:
        incoming = [ref for ref in product.get("source_refs", []) if isinstance(ref, dict)] if isinstance(product.get("source_refs"), list) else []
        items = list(iter_review_values(product))
        anchors: list[tuple[str, str, str | None]] = []
        for ref in incoming:
            sheet, coordinate = _ref_location(ref)
            if sheet in sheets and coordinate and _bounds(coordinate):
                anchors.append((sheet, coordinate, _parent_scope(ref)))
        anchored_sheets = {sheet for sheet, _, _ in anchors}
        allowed_sheets = anchored_sheets if len(anchored_sheets) == 1 else (set(sheets) if not anchors else set())
        reconciled, reconciliation_attempts = _reconciled_structured_refs(
            incoming, items, sheets, allowed_sheets
        )

        tables: list[
            tuple[
                str,
                list[list[Any]],
                int,
                dict[str, list[int]],
                dict[int, dict[str, int]],
                list[int],
            ]
        ] = []
        for sheet, rows in sheets.items():
            if sheet not in allowed_sheets:
                continue
            for header_index, row in enumerate(rows[:20]):
                columns = _columns(row, items)
                if columns:
                    tables.append((
                        sheet,
                        rows,
                        header_index,
                        columns,
                        _option_columns(row),
                        _owned_rows(product, rows, header_index, columns),
                    ))
                    break

        verified: list[dict[str, Any]] = []
        for item in items:
            origin = _origin_for(product, item.value_path)
            if origin in NON_SOURCE_ORIGINS and not is_protected_field(item.value_path, item.field):
                continue
            if origin in {"merchant_edited", "unknown"}:
                continue
            locations: dict[tuple[str, str, str], dict[str, Any]] = {}
            relevant = [ref for ref in incoming if _ref_path(ref) == canonical_value_path(item.value_path)]
            if not relevant and item.scope == "product":
                relevant = [ref for ref in incoming if not ref.get("value_path") and canonical_value_path(ref.get("field")) == canonical_value_path(item.field)]
            if not relevant and item.value_path in reconciled:
                relevant = [reconciled[item.value_path]]
            reconciliation_failed = (
                not relevant and item.value_path in reconciliation_attempts
            )
            parent_refs = [ref for ref in incoming if _parent_scope(ref) == item.scope]
            # Duplicate exact claims are ambiguous even when one happens to verify.
            evidence_refs = (relevant if len(relevant) == 1 else []) + parent_refs
            for ref in evidence_refs:
                sheet, coordinate = _ref_location(ref)
                if sheet not in allowed_sheets or sheet not in sheets:
                    continue
                matched = (
                    _parent_region_match(sheets[sheet], coordinate, item, product)
                    if ref in parent_refs else _region_match(sheets[sheet], coordinate, item)
                )
                if matched:
                    kind, resolved_coordinate = matched
                    roles = _parent_evidence_roles(item) if ref in parent_refs else ("value",)
                    for role in roles:
                        key = (sheet, kind, resolved_coordinate, role)
                        locations[key] = _make_ref(
                            item, sheet, kind, resolved_coordinate,
                            source_file_id or ref.get("source_file_id"), item.value, role,
                        )

            # Reconstruct evidence only from recognized headers and product-owned
            # rows. Continuation rows may have blank Titles, so ownership is
            # established independently from the value being linked.
            if not locations and not relevant and not reconciliation_failed:
                for sheet, rows, header_index, columns, option_columns, owned in tables:
                    if item.scope == "option":
                        # Header forms such as option[size].values are exact
                        # scalar columns; link their cell directly.
                        field_columns = _field_columns(columns, item)
                        for row_index in owned:
                            for col in field_columns:
                                if col < len(rows[row_index]) and values_equal(rows[row_index][col], item.value, numeric_strings=is_numeric_field(item.field)):
                                    coordinate = f"{get_column_letter(col + 1)}{row_index + 1}"
                                    locations[(sheet, "cell", coordinate, "value")] = _make_ref(item, sheet, "cell", coordinate, source_file_id, rows[row_index][col])
                        for ref in _option_refs(
                            item, sheet, rows, owned, option_columns, source_file_id
                        ):
                            kind = "cell_range" if ref.get("cell_range") else "cell"
                            coordinate = str(ref.get(kind) or "")
                            locations[
                                (
                                    sheet,
                                    kind,
                                    coordinate,
                                    str(ref.get("evidence_role") or "value"),
                                )
                            ] = ref
                        continue
                    field_columns = _field_columns(columns, item)
                    if not field_columns:
                        continue
                    candidate_rows = owned
                    variant = _variant_for_item(product, item)
                    if variant is not None:
                        candidate_rows = _variant_row_indices(
                            variant, rows, owned, columns, option_columns
                        )
                        # SKU/tuple identity is usable only when it selects one
                        # row in this table.
                        if len(candidate_rows) != 1:
                            continue
                    elif item.scope == "product" and isinstance(product.get("variants"), list) and len(product["variants"]) == 1:
                        variant_rows = _variant_row_indices(product["variants"][0], rows, owned, columns, option_columns)
                        if len(variant_rows) == 1:
                            candidate_rows = variant_rows
                    elif item.scope == "product":
                        title_col = _single_column(columns, "title")
                        titled = [
                            index for index in owned
                            if title_col is not None
                            and title_col < len(rows[index])
                            and values_equal(rows[index][title_col], product.get("title"))
                        ]
                        if titled:
                            candidate_rows = titled
                    for row_index in candidate_rows:
                        row = rows[row_index]
                        for col in field_columns:
                            if col < len(row) and values_equal(row[col], item.value, numeric_strings=is_numeric_field(item.field)):
                                coordinate = f"{get_column_letter(col + 1)}{row_index + 1}"
                                ref = _make_ref(
                                    item, sheet, "cell", coordinate,
                                    source_file_id, row[col],
                                )
                                locations[(sheet, "cell", coordinate, "value")] = ref
            if locations:
                # Multiple role-distinct exact contributions are intentional.
                # A scalar value itself must still resolve to one exact cell.
                value_locations = [
                    ref for ref in locations.values()
                    if ref.get("evidence_role", "value") == "value"
                ]
                if item.scope == "option" or len(value_locations) == 1:
                    verified.extend(locations.values())
        deduplicated: dict[tuple[Any, ...], dict[str, Any]] = {}
        for ref in verified:
            bbox = ref.get("bbox")
            identity = (
                ref.get("source_file_id"),
                ref.get("sheet"),
                ref.get("cell"),
                ref.get("cell_range"),
                ref.get("page"),
                tuple(bbox) if isinstance(bbox, list) else None,
                ref.get("evidence_role", "value"),
                ref.get("value_path"),
            )
            deduplicated.setdefault(identity, ref)
        product["source_refs"] = list(deduplicated.values())
        verified_paths = {
            canonical_value_path(ref.get("value_path"))
            for ref in product["source_refs"]
            if isinstance(ref, dict) and ref.get("value_path")
        }
        origins = product.get("value_origins")
        if not isinstance(origins, dict):
            origins = {}
        for path in verified_paths:
            if is_protected_field(path):
                origins[path] = "source"
        if origins:
            product["value_origins"] = origins
        remove_unverified_protected_values(product, verified_paths)
