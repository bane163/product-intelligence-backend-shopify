"""Canonical encrypted Shopify session access backed by Supabase."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from supabase_client import get_supabase


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _keys() -> list[bytes]:
    development_fallback = (
        os.getenv("SHOPIFY_API_SECRET", "").strip()
        if os.getenv("ENVIRONMENT", "").strip().lower() != "production"
        else ""
    )
    values = [
        os.getenv("SHOPIFY_TOKEN_ENCRYPTION_KEY", "").strip(),
        os.getenv("SHOPIFY_TOKEN_ENCRYPTION_KEY_PREVIOUS", "").strip(),
        development_fallback,
    ]
    keys = [hashlib.sha256(value.encode("utf-8")).digest() for value in values if value]
    if not keys:
        raise RuntimeError("SHOPIFY_TOKEN_ENCRYPTION_KEY is required in production")
    return keys


def _associated_data(session_id: str, shop_domain: str) -> bytes:
    return f"stockpile:shopify-session:{session_id}:{shop_domain.lower()}".encode("utf-8")


def decrypt_session(envelope: str, session_id: str, shop_domain: str) -> dict[str, Any]:
    parts = str(envelope or "").split(".")
    if len(parts) != 4 or parts[0] != "v1":
        raise RuntimeError("Unsupported encrypted Shopify session payload")
    iv, tag, ciphertext = (_decode(value) for value in parts[1:])
    for key in _keys():
        try:
            plaintext = AESGCM(key).decrypt(
                iv,
                ciphertext + tag,
                _associated_data(session_id, shop_domain),
            )
            payload = json.loads(plaintext.decode("utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            continue
    raise RuntimeError("Unable to decrypt Shopify session")


def get_offline_access_token(shop: str) -> str | None:
    shop_domain = str(shop or "").strip().lower()
    if not shop_domain:
        return None
    session_id = f"offline_{shop_domain}"
    response = (
        get_supabase()
        .table("shopify_sessions")
        .select("id,shop_domain,session_ciphertext")
        .eq("id", session_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    row = response.data[0]
    payload = decrypt_session(
        str(row.get("session_ciphertext") or ""),
        str(row.get("id") or session_id),
        str(row.get("shop_domain") or shop_domain),
    )
    token = payload.get("accessToken")
    return token.strip() if isinstance(token, str) and token.strip() else None
