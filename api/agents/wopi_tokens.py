"""Short-lived signed WOPI access tokens shared with the embedded app."""

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, Request


def _decode_part(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def validate_wopi_token(request: Request, file_id: str) -> dict[str, Any]:
    token = request.query_params.get("access_token", "")
    try:
        payload_part, signature_part = token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Missing WOPI token") from exc

    key = os.getenv("WOPI_SIGNING_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="WOPI signing is not configured")
    expected = hmac.new(key.encode(), payload_part.encode(), hashlib.sha256).digest()
    try:
        supplied = _decode_part(signature_part)
        payload = json.loads(_decode_part(payload_part))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid WOPI token") from exc
    if not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="Invalid WOPI token")
    if str(payload.get("file_id") or "") != file_id:
        raise HTTPException(status_code=403, detail="WOPI token file mismatch")
    if int(payload.get("exp") or 0) <= int(time.time()):
        raise HTTPException(status_code=401, detail="WOPI token expired")
    if payload.get("permission") not in {"view", "edit"}:
        raise HTTPException(status_code=403, detail="Invalid WOPI permission")
    post_message_origin = payload.get("post_message_origin")
    if post_message_origin:
        parsed_origin = urlparse(str(post_message_origin))
        if (
            parsed_origin.scheme not in {"http", "https"}
            or not parsed_origin.netloc
            or parsed_origin.path not in {"", "/"}
            or parsed_origin.params
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            raise HTTPException(status_code=403, detail="Invalid WOPI postMessage origin")
    return payload
