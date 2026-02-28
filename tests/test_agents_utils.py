from urllib.parse import urlencode

from fastapi import HTTPException
from starlette.requests import Request

from api.agents.utils import (
    normalize_shop_domain,
    require_shop_domain,
    resolve_shop_access_token,
    resolve_shop_domain,
)


def _make_request(
    headers: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
) -> Request:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    query_string = urlencode(query or {}).encode("latin-1")
    return Request({"type": "http", "headers": raw_headers, "query_string": query_string})


def testnormalize_shop_domain_trims_and_lowercases():
    assert (
        normalize_shop_domain(" TEST-SHOP.myshopify.com ") == "test-shop.myshopify.com"
    )
    assert normalize_shop_domain("   ") is None
    assert normalize_shop_domain(None) is None


def test_resolve_shop_domain_rejects_mismatch():
    request = _make_request({"x-shop-domain": "a.myshopify.com"})
    try:
        resolve_shop_domain(request, "b.myshopify.com")
        raise AssertionError("Expected HTTPException for mismatched shop domains")
    except HTTPException as exc:
        assert exc.status_code == 403
        assert exc.detail == "shop_domain mismatch"


def testrequire_shop_domain_raises_for_missing_value():
    request = _make_request()
    try:
        require_shop_domain(request, None)
        raise AssertionError("Expected HTTPException when shop_domain is missing")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "Missing shop_domain"


def testrequire_shop_domain_uses_body_when_header_absent():
    request = _make_request()
    assert require_shop_domain(request, "Store.myshopify.com") == "store.myshopify.com"


def testrequire_shop_domain_uses_query_shop_when_header_absent():
    request = _make_request(query={"shop": "Store.myshopify.com"})
    assert require_shop_domain(request, None) == "store.myshopify.com"


def test_resolve_shop_domain_rejects_header_and_query_mismatch():
    request = _make_request(
        headers={"x-shop-domain": "a.myshopify.com"},
        query={"shop": "b.myshopify.com"},
    )
    try:
        require_shop_domain(request, None)
        raise AssertionError("Expected HTTPException for mismatched shop domains")
    except HTTPException as exc:
        assert exc.status_code == 403
        assert exc.detail == "shop_domain mismatch"


def testresolve_shop_access_token_prefers_header():
    request = _make_request({"x-shop-access-token": " header-token "})
    assert resolve_shop_access_token(request, "body-token") == "header-token"
    assert resolve_shop_access_token(_make_request(), " body-token ") == "body-token"
    assert resolve_shop_access_token(_make_request(), None) is None
