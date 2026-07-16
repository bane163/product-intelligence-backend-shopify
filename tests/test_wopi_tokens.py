import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.agents.wopi_tokens import validate_wopi_token


def _token(key: str, payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(hmac.new(key.encode(), encoded.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"{encoded}.{signature}"


def _request(token: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": f"access_token={token}".encode(), "headers": []})


def test_wopi_token_validates_file_permission_and_expiry(monkeypatch):
    monkeypatch.setenv("WOPI_SIGNING_KEY", "test-key")
    token = _token("test-key", {"file_id": "file-1", "shop": "demo.myshopify.com", "permission": "view", "exp": int(time.time()) + 60})
    assert validate_wopi_token(_request(token), "file-1")["permission"] == "view"


def test_wopi_token_rejects_file_mismatch(monkeypatch):
    monkeypatch.setenv("WOPI_SIGNING_KEY", "test-key")
    token = _token("test-key", {"file_id": "file-1", "permission": "view", "exp": int(time.time()) + 60})
    with pytest.raises(HTTPException) as exc:
        validate_wopi_token(_request(token), "file-2")
    assert exc.value.status_code == 403
