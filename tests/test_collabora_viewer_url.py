import pytest

import cloudflare_tunnel
from services.collabora_service import CollaboraService


def test_viewer_payload_prefers_https_tunnel(monkeypatch):
    monkeypatch.setattr(
        cloudflare_tunnel,
        "get_tunnel_url",
        lambda: "https://viewer.trycloudflare.com",
    )
    monkeypatch.setenv("COLLABORA_URL", "http://collabora:9980")
    monkeypatch.delenv("COLLABORA_PUBLIC_URL", raising=False)

    payload = CollaboraService().get_collabora_url_payload()

    assert payload["collabora_url"] == "https://viewer.trycloudflare.com"
    assert payload["is_tunnel"] is True
    assert payload["wopi_base_url"].startswith("http://shopify-backend:8000/")


def test_viewer_payload_rejects_internal_url(monkeypatch):
    monkeypatch.setattr(cloudflare_tunnel, "get_tunnel_url", lambda: None)
    monkeypatch.setenv("COLLABORA_URL", "http://collabora:9980")
    monkeypatch.delenv("COLLABORA_PUBLIC_URL", raising=False)

    with pytest.raises(RuntimeError, match="not available yet"):
        CollaboraService().get_collabora_url_payload()


def test_viewer_payload_accepts_configured_public_https_url(monkeypatch):
    monkeypatch.setattr(cloudflare_tunnel, "get_tunnel_url", lambda: None)
    monkeypatch.setenv("COLLABORA_PUBLIC_URL", "https://office.example.com/")

    payload = CollaboraService().get_collabora_url_payload()

    assert payload["collabora_url"] == "https://office.example.com"
    assert payload["is_tunnel"] is False


@pytest.mark.parametrize(
    "value",
    [
        "http://collabora:9980",
        "http://localhost:9980",
        "https://collabora:9980",
        "https://127.0.0.1:9980",
        "not-a-url",
    ],
)
def test_viewer_payload_rejects_non_https_public_configuration(monkeypatch, value):
    monkeypatch.setattr(cloudflare_tunnel, "get_tunnel_url", lambda: None)
    monkeypatch.setenv("COLLABORA_PUBLIC_URL", value)

    with pytest.raises(RuntimeError, match="public HTTPS URL"):
        CollaboraService().get_collabora_url_payload()
