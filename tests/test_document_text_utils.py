import io
import zipfile

from ai.document_text_utils import (
    extract_docx_contents,
    extract_pdf_contents,
    extract_pptx_contents,
)


def _docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            """
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:r><w:t>First line</w:t></w:r></w:p>
                <w:p><w:r><w:t>Second line</w:t></w:r></w:p>
              </w:body>
            </w:document>
            """,
        )
    return buffer.getvalue()


def _pptx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            """
            <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                   xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld>
                <p:spTree>
                  <p:sp><p:txBody><a:p><a:r><a:t>Slide text</a:t></a:r></a:p></p:txBody></p:sp>
                </p:spTree>
              </p:cSld>
            </p:sld>
            """,
        )
    return buffer.getvalue()


def test_extract_docx_contents():
    text = extract_docx_contents(_docx_bytes())
    assert "First line" in text
    assert "Second line" in text


def test_extract_pptx_contents():
    text = extract_pptx_contents(_pptx_bytes())
    assert "Slide text" in text


def test_extract_pdf_contents():
    text = extract_pdf_contents(b"%PDF-1.4 (Alpha) Tj (Beta) ET")
    assert "Alpha" in text
    assert "Beta" in text
