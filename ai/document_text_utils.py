import io
import re
import zipfile
import xml.etree.ElementTree as ET


DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
PPTX_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


def _truncate_lines(lines: list[str], *, max_lines: int, max_chars: int) -> str:
    cleaned: list[str] = []
    total_chars = 0
    for line in lines:
        value = line.strip()
        if not value:
            continue
        total_chars += len(value)
        if len(cleaned) >= max_lines or total_chars > max_chars:
            cleaned.append("(...truncated...)")
            break
        cleaned.append(value)
    return "\n".join(cleaned)


def _decode_pdf_literal(value: bytes) -> str:
    text = value.decode("latin-1", errors="ignore")
    text = text.replace(r"\n", "\n").replace(r"\r", "\n").replace(r"\t", "\t")
    text = text.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
    return text.strip()


def extract_pdf_contents(
    file_bytes: bytes,
    *,
    max_fragments: int = 600,
    max_chars: int = 50_000,
) -> str:
    # Lightweight fallback parser: extracts literal strings from PDF content streams.
    literals = re.findall(rb"\(([^()]*)\)", file_bytes)
    decoded = [_decode_pdf_literal(item) for item in literals]
    return _truncate_lines(decoded, max_lines=max_fragments, max_chars=max_chars)


def extract_docx_contents(
    file_bytes: bytes,
    *,
    max_lines: int = 800,
    max_chars: int = 50_000,
) -> str:
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    lines = [
        (node.text or "").strip()
        for node in root.findall(".//w:t", DOCX_NS)
        if node.text and node.text.strip()
    ]
    return _truncate_lines(lines, max_lines=max_lines, max_chars=max_chars)


def extract_pptx_contents(
    file_bytes: bytes,
    *,
    max_slides: int = 100,
    max_lines: int = 1_000,
    max_chars: int = 50_000,
) -> str:
    lines: list[str] = []
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
        slide_paths = sorted(
            (
                name
                for name in archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            ),
            key=lambda name: (
                int(
                    re.search(r"slide(\d+)\.xml$", name).group(1)  # type: ignore[union-attr]
                )
                if re.search(r"slide(\d+)\.xml$", name)
                else 0
            ),
        )
        for index, slide_path in enumerate(slide_paths, start=1):
            if index > max_slides:
                lines.append("(...truncated...)")
                break
            lines.append(f"--- Slide {index} ---")
            slide_xml = archive.read(slide_path)
            root = ET.fromstring(slide_xml)
            slide_lines = [
                (node.text or "").strip()
                for node in root.findall(".//a:t", PPTX_NS)
                if node.text and node.text.strip()
            ]
            lines.extend(slide_lines)

    return _truncate_lines(lines, max_lines=max_lines, max_chars=max_chars)


def extract_document_contents(file_bytes: bytes, *, document_kind: str) -> str:
    if document_kind == "pdf":
        return extract_pdf_contents(file_bytes)
    if document_kind == "docx":
        return extract_docx_contents(file_bytes)
    if document_kind == "pptx":
        return extract_pptx_contents(file_bytes)
    return ""
