from application.domain.extraction_quality import build_quality_report


def _codes(report):
    return {issue["code"] for issue in report["issues"]}


def test_quality_gate_blocks_missing_sellable_data_and_warns_on_optional_data():
    report = build_quality_report([{"title": "", "variants": [{}]}])

    assert {"TITLE_REQUIRED", "SKU_REQUIRED", "PRICE_REQUIRED"} <= _codes(report)
    assert {"VENDOR_MISSING", "IMAGE_MISSING"} <= _codes(report)
    assert report["summary"] == {
        "product_count": 1,
        "valid_product_count": 0,
        "blocked_product_count": 1,
        "blocker_count": 3,
        "warning_count": 2,
    }


def test_quality_gate_keeps_valid_products_and_detects_cross_import_duplicates():
    products = [
        {
            "title": "One",
            "handle": "one",
            "vendor": "Acme",
            "image_src": "https://example.com/one.jpg",
            "variants": [{"sku": "SKU-1", "price": 10}],
            "source_refs": [{"value_path": "title", "cell": "A2"}],
        },
        {
            "title": "Two",
            "handle": "one",
            "vendor": "Acme",
            "image_src": "https://example.com/two.jpg",
            "variants": [{"sku": "sku-1", "price": 12}],
            "source_refs": [{"value_path": "title", "cell": "A3"}],
        },
    ]

    report = build_quality_report(products)

    assert {"DUPLICATE_IMPORT_HANDLE", "DUPLICATE_IMPORT_SKU"} <= _codes(report)
    assert report["summary"]["valid_product_count"] == 1


def test_missing_evidence_is_warning_only():
    report = build_quality_report([
        {
            "title": "Valid",
            "vendor": "Acme",
            "image_src": "https://example.com/image.jpg",
            "variants": [{"sku": "SKU-1", "price": "0.00"}],
        }
    ])

    evidence = next(issue for issue in report["issues"] if issue["code"] == "SOURCE_EVIDENCE_MISSING")
    assert evidence["severity"] == "warning"
    assert report["summary"]["blocked_product_count"] == 0


def test_reserved_test_image_does_not_count_as_extracted_media():
    report = build_quality_report([{
        "title": "Valid",
        "vendor": "Acme",
        "image_src": "https://example.com/image.jpg",
        "variants": [{"sku": "SKU-1", "price": "10.00"}],
    }])
    assert "IMAGE_MISSING" in _codes(report)
