from application.domain.product_images import (
    product_set_files,
    public_https_url,
    review_media_gallery,
    resolve_extracted_image,
    resolve_product_image,
)
from application.domain.shopify_product_normalization import build_product_set_input
from application.domain.shopify_mutation_plan import build_review_preview


def test_extracted_image_order_dedup_and_alt_text():
    product = {
        "title": "Trail shoe",
        "image_src": "https://example.com/rejected.jpg",
        "image_alt_text": "Rejected",
        "images": [
            {"src": "https://cdn.shopify.com/first.jpg", "alt": "First", "position": 1},
            {
                "src": "https://cdn.shopify.com/second.jpg",
                "alt": "Second",
                "position": 2,
            },
        ],
    }
    assert resolve_extracted_image(product) == {
        "url": "https://cdn.shopify.com/first.jpg",
        "alt_text": "First",
        "origin": "extracted",
    }


def test_rejects_non_public_https_urls_without_fetching():
    rejected = [
        "http://cdn.example.dev/image.jpg",
        "https://localhost/image.jpg",
        "https://127.0.0.1/image.jpg",
        "https://10.0.0.2/image.jpg",
        "https://example.com/image.jpg",
        "not a url",
    ]
    assert all(public_https_url(url) is None for url in rejected)
    assert public_https_url("https://cdn.shopify.com/image.jpg") is not None


def test_missing_image_never_receives_a_synthetic_fallback():
    product = {"handle": "trail-shoe", "title": "Trail shoe"}
    assert resolve_product_image(product, None, non_production=True) is None
    assert resolve_product_image(product, None, non_production=False) is None


def test_review_gallery_includes_existing_and_embedded_media():
    live = {
        "media": {
            "nodes": [
                {
                    "id": "gid://shopify/MediaImage/1",
                    "image": {
                        "url": "https://cdn.shopify.com/old.jpg",
                        "altText": "Old",
                    },
                }
            ]
        }
    }
    product = {
        "media_gallery": [
            {
                "review_media_id": "asset-1",
                "asset_id": "asset-1",
                "source_kind": "pdf_embedded",
                "validation_status": "ready",
                "assignment_status": "assigned",
                "included": True,
                "position": 2,
                "alt_text": "New",
            }
        ]
    }

    gallery = review_media_gallery(product, live)

    assert gallery[0]["source_kind"] == "shopify_existing"
    assert gallery[0]["locked"] is True
    assert gallery[1]["preview_url"] == "/api/media-assets/asset-1"


def test_product_set_files_preserve_existing_ids_and_serialize_external_image():
    live = {"media": {"nodes": [{"id": "gid://shopify/MediaImage/1"}]}}
    image = {
        "url": "https://cdn.shopify.com/trail.jpg",
        "alt_text": "Trail shoe",
        "origin": "extracted",
    }
    files = product_set_files(image, live)
    assert files == [
        {"id": "gid://shopify/MediaImage/1"},
        {
            "contentType": "IMAGE",
            "originalSource": "https://cdn.shopify.com/trail.jpg",
            "alt": "Trail shoe",
        },
    ]
    payload = build_product_set_input({"title": "Trail shoe", "files": files})
    assert payload["files"] == files


def test_existing_shopify_image_is_previewed_without_resynchronizing_files():
    live = {
        "featuredImage": {"url": "https://cdn.shopify.com/old.jpg", "altText": "Old"},
        "media": {"nodes": [{"id": "gid://shopify/MediaImage/1"}]},
    }
    image = resolve_product_image({"title": "Existing"}, live, non_production=True)
    assert image and image["origin"] == "shopify"
    assert product_set_files(image, live) is None


def test_review_preview_projects_server_resolved_image():
    image = {
        "url": "https://cdn.shopify.com/trail.jpg",
        "alt_text": "Trail shoe",
        "origin": "extracted",
    }
    preview = build_review_preview(
        {
            "item_id": "item-1",
            "selected": True,
            "proposed": {"title": "Trail shoe"},
            "review_changes": [],
            "image": image,
        }
    )
    assert preview["image"] == image
