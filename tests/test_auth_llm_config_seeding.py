from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from httpx import ASGITransport, AsyncClient

import auth
from main import app
from services.supabase_service import SupabaseService


class _FakeLlmConfigsNamespace:
    def __init__(self) -> None:
        self._configs: list[dict] = []
        self.created: list[dict] = []

    def list_llm_model_configs(self, shop_domain: str) -> list[dict]:
        tenant = str(shop_domain or "").strip().lower()
        return [
            dict(item)
            for item in self._configs
            if str(item.get("shop_domain") or "").strip().lower() == tenant
        ]

    def create_llm_model_config(self, **payload):
        row = {"id": f"cfg-{len(self._configs) + 1}", **payload}
        self._configs.append(dict(row))
        self.created.append(dict(row))
        return row


def test_seed_default_llm_configs_uses_env_fallback_when_vault_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm_namespace = _FakeLlmConfigsNamespace()
    fake_ctx = SimpleNamespace(
        supabase=SimpleNamespace(llm_configs=llm_namespace),
    )

    import app_context

    monkeypatch.setattr(app_context, "get_app_context", lambda: fake_ctx)
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-secret-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret-key")
    monkeypatch.setenv("OLLAMA_CLOUD_URL", "https://ollama.example/v1")
    monkeypatch.setattr(auth, "_resolve_vault_secret", lambda _name: None)

    auth._seed_default_llm_configs_on_install("Store.MyShopify.com", include_ollama=True)
    auth._seed_default_llm_configs_on_install("Store.MyShopify.com", include_ollama=True)

    created = llm_namespace.created
    assert len(created) == 2
    ollama = next(item for item in created if "ollama" in item["provider"])
    openai = next(item for item in created if item["provider"] == "openai")

    assert ollama["api_key"] == ""
    assert openai["api_key"] == "openai-secret-key"
    assert ollama["is_active"] is True
    assert openai["is_active"] is False
    assert ollama["extra"]["api_key_env_var"] == "OLLAMA_API_KEY"
    assert openai["extra"]["api_key_source"] == "env_fallback"
    assert openai["extra"]["api_key_env_var"] == "OPENAI_API_KEY"


def test_get_active_llm_model_config_resolves_env_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SupabaseService()
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    shop = "store.myshopify.com"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")

    service.create_llm_model_config(
        shop_domain=shop,
        name="OpenAI (Default)",
        provider="openai",
        base_url="https://api.openai.com/v1",
        model_id="gpt-4.1-mini",
        api_key="",
        is_active=True,
        extra={
            "api_key_source": "env_ref",
            "api_key_env_var": "OPENAI_API_KEY",
            "seeded_by": "shopify_auth_install",
        },
    )

    stored = next(iter(service.llm_model_configs.values()))
    assert stored["api_key_ciphertext"] == ""
    assert stored["api_key_last4"] is None

    active = service.get_active_llm_model_config(shop)
    assert active is not None
    assert active["api_key"] == "sk-test-openai"
    assert active["api_key_masked"] == "env:OPENAI_API_KEY"


@pytest.mark.asyncio
async def test_legacy_auth_callback_is_not_mounted() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/shopify/auth/callback")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_legacy_auth_install_is_not_mounted() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/shopify/auth/install")
    assert response.status_code == 404


def test_seed_default_llm_configs_can_use_vault_key_with_shop_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm_namespace = _FakeLlmConfigsNamespace()
    fake_ctx = SimpleNamespace(
        supabase=SimpleNamespace(llm_configs=llm_namespace),
    )

    import app_context

    monkeypatch.setattr(app_context, "get_app_context", lambda: fake_ctx)

    names_seen: list[str] = []

    def fake_vault_lookup(secret_name: str) -> str | None:
        names_seen.append(secret_name)
        if secret_name == "openai_api_key__store.myshopify.com":
            return "sk-vault-shop"
        return None

    monkeypatch.setattr(auth, "_resolve_vault_secret", fake_vault_lookup)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = auth._seed_default_llm_configs_on_install(
        "Store.MyShopify.com",
        include_ollama=False,
        seed_source="llm_seed_endpoint",
    )

    assert result["created"] == 1
    assert result["openai_key_source"] == "vault"
    assert result["openai_vault_secret_name"] == "openai_api_key__store.myshopify.com"
    assert names_seen[0] == "openai_api_key__store.myshopify.com"

    openai = llm_namespace.created[0]
    assert openai["provider"] == "openai"
    assert openai["api_key"] == "sk-vault-shop"
    assert openai["is_active"] is True
    assert openai["extra"]["api_key_source"] == "vault"
    assert openai["extra"]["vault_secret_name"] == "openai_api_key__store.myshopify.com"


@pytest.mark.asyncio
async def test_llm_seed_endpoint_calls_backend_seed_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_seed(shop: str, **kwargs):
        captured["shop"] = shop
        captured.update(kwargs)
        return {
            "created": 1,
            "skipped": 0,
            "openai_key_source": "vault",
            "openai_vault_secret_name": "openai_api_key__seed-shop.myshopify.com",
        }

    monkeypatch.setattr(auth, "_seed_default_llm_configs_on_install", fake_seed)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/agents/llm-configs/seed",
            json={
                "shop_domain": "seed-shop.myshopify.com",
                "defaults": {
                    "name": "OpenAI",
                    "provider": "openai",
                    "base_url": "https://api.openai.com/v1",
                    "model_id": "gpt-5-mini-2025-08-07",
                    "temperature": 0.2,
                    "max_tokens": 64000,
                    "timeout_seconds": 120,
                    "enable_file_search": False,
                    "is_active": True,
                },
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] == 1
    assert payload["openai_key_source"] == "vault"
    assert captured["shop"] == "seed-shop.myshopify.com"
    assert captured["include_ollama"] is False
    assert captured["seed_source"] == "llm_seed_endpoint"


@pytest.mark.asyncio
async def test_llm_seed_endpoint_uses_global_vault_fallback_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm_namespace = _FakeLlmConfigsNamespace()
    fake_ctx = SimpleNamespace(
        supabase=SimpleNamespace(llm_configs=llm_namespace),
    )

    import app_context

    monkeypatch.setattr(app_context, "get_app_context", lambda: fake_ctx)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        auth,
        "_resolve_vault_secret",
        lambda name: "sk-global-vault" if name == "openai_api_key" else None,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        first = await client.post(
            "/agents/llm-configs/seed",
            json={"shop_domain": "seed-shop.myshopify.com"},
        )
        second = await client.post(
            "/agents/llm-configs/seed",
            json={"shop_domain": "seed-shop.myshopify.com"},
        )

    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["created"] == 1
    assert first_payload["openai_key_source"] == "vault"
    assert first_payload["openai_vault_secret_name"] == "openai_api_key"

    assert llm_namespace.created[0]["provider"] == "openai"
    assert llm_namespace.created[0]["api_key"] == "sk-global-vault"

    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["created"] == 0
    assert second_payload["skipped"] == 1
