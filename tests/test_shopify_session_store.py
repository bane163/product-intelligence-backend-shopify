import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import shopify_session_store


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def test_decrypts_the_shared_node_session_envelope(monkeypatch):
    monkeypatch.setenv("SHOPIFY_TOKEN_ENCRYPTION_KEY", "shared-test-key")
    session_id = "offline_store.myshopify.com"
    shop = "store.myshopify.com"
    payload = {"id": session_id, "shop": shop, "accessToken": "shpat_secret"}
    iv = bytes(range(12))
    key = hashlib.sha256(b"shared-test-key").digest()
    encrypted = AESGCM(key).encrypt(
        iv,
        json.dumps(payload, separators=(",", ":")).encode(),
        f"stockpile:shopify-session:{session_id}:{shop}".encode(),
    )
    envelope = f"v1.{_b64(iv)}.{_b64(encrypted[-16:])}.{_b64(encrypted[:-16])}"
    assert shopify_session_store.decrypt_session(envelope, session_id, shop) == payload


def test_decryption_fails_closed_with_wrong_identity(monkeypatch):
    monkeypatch.setenv("SHOPIFY_TOKEN_ENCRYPTION_KEY", "shared-test-key")
    try:
        shopify_session_store.decrypt_session("v1.AA.AA.AA", "wrong", "wrong")
        raise AssertionError("expected decryption failure")
    except RuntimeError as exc:
        assert "decrypt" in str(exc).lower()
