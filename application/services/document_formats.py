from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

DocumentKind = Literal[
    "csv",
    "spreadsheet",
    "spreadsheet_legacy",
    "pdf",
    "docx",
    "pptx",
    "unsupported",
]

EXTENSION_TO_KIND: dict[str, DocumentKind] = {
    ".csv": "csv",
    ".xlsx": "spreadsheet",
    ".xlsm": "spreadsheet",
    ".xls": "spreadsheet_legacy",
    ".ods": "spreadsheet_legacy",
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
}

CONTENT_TYPE_TO_KIND: dict[str, DocumentKind] = {
    "text/csv": "csv",
    "application/csv": "csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "spreadsheet",
    "application/vnd.ms-excel.sheet.macroenabled.12": "spreadsheet",
    "application/vnd.ms-excel": "spreadsheet_legacy",
    "application/vnd.oasis.opendocument.spreadsheet": "spreadsheet_legacy",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}

SUPPORTED_EXTENSIONS = tuple(sorted(EXTENSION_TO_KIND.keys()))


@dataclass(frozen=True)
class DocumentFormat:
    kind: DocumentKind
    extension: str | None
    content_type: str

    @property
    def is_supported(self) -> bool:
        return self.kind != "unsupported"

    @property
    def is_spreadsheet(self) -> bool:
        return self.kind in {"csv", "spreadsheet", "spreadsheet_legacy"}

    @property
    def requires_xlsx_conversion(self) -> bool:
        return self.kind in {"csv", "spreadsheet_legacy"}


def normalize_content_type(content_type: str | None) -> str:
    if not content_type:
        return ""
    return str(content_type).split(";", 1)[0].strip().lower()


def detect_extension(filename: str | None) -> str | None:
    if not filename:
        return None
    _, ext = os.path.splitext(str(filename).strip().lower())
    return ext or None


def classify_document(
    *,
    filename: str | None = None,
    content_type: str | None = None,
    file_bytes: bytes | None = None,
) -> DocumentFormat:
    extension = detect_extension(filename)
    normalized_content_type = normalize_content_type(content_type)

    kind: DocumentKind = "unsupported"
    if extension and extension in EXTENSION_TO_KIND:
        kind = EXTENSION_TO_KIND[extension]
    elif normalized_content_type in CONTENT_TYPE_TO_KIND:
        kind = CONTENT_TYPE_TO_KIND[normalized_content_type]
    elif file_bytes:
        if file_bytes.startswith(b"%PDF"):
            kind = "pdf"
        elif file_bytes.startswith(b"PK\x03\x04"):
            # ZIP container (xlsx/xlsm/docx/pptx); extension/MIME are preferred.
            kind = "spreadsheet"

    return DocumentFormat(
        kind=kind,
        extension=extension,
        content_type=normalized_content_type,
    )


def supported_extensions_display() -> str:
    return ", ".join(SUPPORTED_EXTENSIONS)
