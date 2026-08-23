from io import BytesIO
from types import SimpleNamespace

import fitz
import pytest
from PIL import Image

from application.services.product_media_ingestion import (
    PRODUCT_MEDIA_FILE_ORIGIN,
    ProductMediaFetchError,
    _normalize_image,
    attach_product_media,
    create_external_media_item,
)


class RecordingFiles:
    def __init__(self):
        self.saved: list[dict] = []

    def save_file(self, file_id, **fields):
        self.saved.append({"file_id": file_id, **fields})


def image_bytes(size=(240, 240), color=(38, 86, 139)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def pdf_with_product_image() -> bytes:
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    page.insert_image(fitz.Rect(60, 140, 300, 380), stream=image_bytes())
    try:
        return document.tobytes()
    finally:
        document.close()


@pytest.mark.asyncio
async def test_embedded_pdf_image_is_persisted_and_assigned_to_review_gallery():
    files = RecordingFiles()
    products = [
        {
            "title": "Aura Linen Shirt",
            "source_refs": [
                {"value_path": "title", "page": 1, "bbox": [0.1, 0.05, 0.8, 0.1]}
            ],
        }
    ]

    stats = await attach_product_media(
        supabase=SimpleNamespace(file=files),
        products=products,
        source_file_id="catalog-pdf",
        shop_domain="media.myshopify.com",
        pdf_content=pdf_with_product_image(),
    )

    assert stats == {"ready": 1, "invalid": 0, "ambiguous": 0, "embedded": 1}
    assert len(files.saved) == 1
    assert files.saved[0]["file_origin"] == PRODUCT_MEDIA_FILE_ORIGIN
    assert files.saved[0]["shop_domain"] == "media.myshopify.com"
    media = products[0]["media_gallery"][0]
    assert media["source_kind"] == "embedded_pdf"
    assert media["validation_status"] == "ready"
    assert media["assignment_status"] == "assigned"
    assert media["included"] is True
    assert media["asset_id"] == files.saved[0]["file_id"]


@pytest.mark.asyncio
async def test_placeholder_image_url_is_visible_as_invalid_and_never_downloaded():
    files = RecordingFiles()
    media = await create_external_media_item(
        supabase=SimpleNamespace(file=files),
        source_file_id="catalog-pdf",
        shop_domain="media.myshopify.com",
        source_url="https://example.com/aura-linen-shirt.jpg",
        alt_text="Aura Linen Shirt",
        position=1,
    )

    assert media["validation_status"] == "invalid"
    assert media["included"] is False
    assert media["issue_code"] == "IMAGE_URL_INVALID"
    assert files.saved == []


def test_image_normalization_rejects_shopify_dimension_limit_before_decode():
    with pytest.raises(ProductMediaFetchError, match="Shopify's product-image"):
        _normalize_image(image_bytes(size=(4473, 1)))
