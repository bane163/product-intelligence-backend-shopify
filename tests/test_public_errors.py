"""Public failure boundaries protect live and historical diagnostic text."""

import copy
import pytest
from httpx import ASGITransport, AsyncClient
from main import app
from shared.public_errors import sanitize_payload, safe_detail


@pytest.mark.parametrize(
    "raw",
    [
        "Access denied for fileErrors field. Required access: read_files",
        "https://private.example/?token=secret",
        "relation internal_table does not exist: SELECT secret",
        "TimeoutError: internal.host timed out after 30000ms",
        "<html>malformed upstream response</html>",
        "unknown private exception",
    ],
)
def test_failure_text_is_never_display_copy(raw):
    result = sanitize_payload(
        {"status": "failed", "message": raw, "error": raw, "failure_message": raw}
    )
    assert raw not in str(result)
    assert raw not in str(safe_detail(raw, 500))
    assert result["status"] == "failed"


def test_validation_preserves_focus_identifiers_and_sanitizes_provider_messages():
    result = safe_detail(
        {
            "detail": {
                "code": "REVIEW_FIELD_VALIDATION_FAILED",
                "message": "private sql",
                "field_errors": [
                    {
                        "code": "INVALID_PRICE",
                        "item_id": "item",
                        "change_id": "price",
                        "value_path": "variants[0].price",
                        "message": "database diagnostic",
                    }
                ],
            }
        }
    )
    error = result["field_errors"][0]
    assert error["item_id"] == "item"
    assert error["change_id"] == "price"
    assert error["value_path"] == "variants[0].price"
    assert "valid price" in error["message"]
    assert "database" not in str(result)


def test_partial_and_historical_failures_are_sanitized_without_mutating_records():
    old = {
        "status": "partially_applied",
        "product_id": "gid://shopify/Product/1",
        "error": "private exception",
        "errors": [{"field": "price", "message": "secret SQL"}],
    }
    before = copy.deepcopy(old)
    result = sanitize_payload(old)
    assert old == before
    assert result["status"] == "partially_applied"
    assert result["product_id"] == before["product_id"]
    assert result["error"].startswith("Some changes were applied")
    assert "secret" not in str(result)


@pytest.mark.asyncio
async def test_http_boundary_sanitizes_success_embedded_failures_and_exception_responses():
    original = list(app.router.routes)

    @app.get("/__public_failure_test")
    async def embedded_failure():
        return {
            "status": "failed",
            "error": "private exception",
            "errors": [{"message": "private provider URL"}],
        }

    @app.get("/__public_exception_test")
    async def unexpected_failure():
        raise RuntimeError("private database host")

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            embedded = await client.get("/__public_failure_test")
            unexpected = await client.get("/__public_exception_test")
        assert embedded.status_code == 200
        assert embedded.json()["status"] == "failed"
        assert unexpected.status_code == 500
        assert "private" not in embedded.text + unexpected.text
        assert unexpected.headers.get("x-request-id")
        assert unexpected.headers.get("x-correlation-id")
    finally:
        app.router.routes[:] = original


def test_merchant_json_values_are_not_treated_as_failure_records():
    value = {
        "error": "merchant-defined value",
        "message": "merchant copy",
        "errors": ["customer content"],
    }
    result = sanitize_payload(
        {"proposed": value, "current": value, "error": "secret diagnostic"}
    )
    assert result["proposed"] == value
    assert result["current"] == value
    assert "secret" not in result["error"]


def test_framework_validation_keeps_field_location_without_request_values():
    result = safe_detail(
        {
            "detail": [
                {
                    "loc": ["body", "price"],
                    "type": "float_parsing",
                    "msg": "private error",
                    "input": "secret",
                }
            ]
        },
        422,
    )
    assert result["code"] == "VALIDATION_FAILED"
    assert result["field_errors"][0]["field"] == "price"
    assert result["field_errors"][0]["loc"] == ["body", "price"]
    assert "secret" not in str(result)
    assert "private" not in str(result)
