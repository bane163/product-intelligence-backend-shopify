from io import BytesIO

import pytest
from PIL import Image

from application.services.canonical_pdf import (
    canonical_file_id,
    ensure_canonical_pdf,
    image_to_pdf,
)
from application.services.document_formats import classify_document, validate_document_content


class _Files:
    def __init__(self):
        self.rows = {}

    def get_file(self, file_id):
        return self.rows.get(file_id)

    def save_file(self, file_id, **fields):
        self.rows[file_id] = {**fields, "name": fields["name"], "shop_domain": fields["shop_domain"]}


class _Supabase:
    def __init__(self):
        self.file = _Files()


class _Collabora:
    def __init__(self, result=b"%PDF-bad"):
        self.result = result
        self.calls = 0

    async def convert_document_to_pdf_collabora(self, *args, **kwargs):
        self.calls += 1
        return self.result


def _image_bytes(format_name="PNG", mode="RGBA", size=(120, 60)):
    image = Image.new(mode, size, (255, 0, 0, 128) if mode == "RGBA" else "red")
    output = BytesIO()
    image.save(output, format=format_name)
    return output.getvalue()


@pytest.mark.parametrize(
    ("filename", "mime", "format_name"),
    [("scan.png", "image/png", "PNG"), ("scan.jpg", "image/jpeg", "JPEG"), ("scan.webp", "image/webp", "WEBP")],
)
def test_supported_image_signatures_and_pdf_aspect_ratio(filename, mime, format_name):
    content = _image_bytes(format_name, "RGB")
    document_format = classify_document(filename=filename, content_type=mime, file_bytes=content)
    validate_document_content(document_format, file_bytes=content)
    pdf = image_to_pdf(content)
    import fitz
    document = fitz.open(stream=pdf, filetype="pdf")
    try:
        assert document.page_count == 1
        assert document[0].rect.width / document[0].rect.height == pytest.approx(2, rel=.02)
    finally:
        document.close()


def test_image_signature_mismatch_and_animated_webp_are_rejected():
    jpeg = _image_bytes("JPEG", "RGB")
    fmt = classify_document(filename="scan.png", content_type="image/png", file_bytes=jpeg)
    with pytest.raises(ValueError, match="does not match"):
        validate_document_content(fmt, file_bytes=jpeg)

    frames = [Image.new("RGB", (10, 10), color) for color in ("red", "blue")]
    output = BytesIO()
    frames[0].save(output, "WEBP", save_all=True, append_images=frames[1:])
    with pytest.raises(ValueError, match="multi-frame"):
        image_to_pdf(output.getvalue())


@pytest.mark.asyncio
async def test_canonical_image_is_deterministic_hidden_tenant_owned_and_cached():
    content = _image_bytes()
    supabase = _Supabase()
    fmt = classify_document(filename="scan.png", content_type="image/png", file_bytes=content)
    first = await ensure_canonical_pdf(
        supabase=supabase, collabora=_Collabora(), source_file_id="source-1",
        source_content=content, source_name="scan.png", source_content_type="image/png",
        document_format=fmt, shop_domain="store.example",
    )
    second = await ensure_canonical_pdf(
        supabase=supabase, collabora=_Collabora(), source_file_id="source-1",
        source_content=content, source_name="scan.png", source_content_type="image/png",
        document_format=fmt, shop_domain="store.example",
    )
    assert first.file_id == canonical_file_id("source-1", content)
    assert supabase.file.rows[first.file_id]["file_origin"] == "canonical_pdf"
    assert second.cache_hit is True
