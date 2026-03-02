from types import SimpleNamespace

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


def test_seed_default_llm_configs_uses_non_plaintext_env_refs(
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

    auth._seed_default_llm_configs_on_install("Store.MyShopify.com")
    auth._seed_default_llm_configs_on_install("Store.MyShopify.com")

    created = llm_namespace.created
    assert len(created) == 2
    ollama = next(item for item in created if "ollama" in item["provider"])
    openai = next(item for item in created if item["provider"] == "openai")

    assert ollama["api_key"] == ""
    assert openai["api_key"] == ""
    assert ollama["is_active"] is True
    assert openai["is_active"] is False
    assert ollama["extra"]["api_key_env_var"] == "OLLAMA_API_KEY"
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
async def test_auth_callback_triggers_llm_config_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shop = "seed-shop.myshopify.com"
    state = "state-seed-llm"
    auth._state_store[state] = shop

    seeded: dict[str, str] = {}
    monkeypatch.setattr(auth, "_get_client_credentials", lambda: ("id", "secret"))
    monkeypatch.setattr(auth, "_verify_hmac", lambda params, client_secret: True)
    monkeypatch.setattr(auth.token_store, "save_token", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        auth,
        "_seed_default_llm_configs_on_install",
        lambda shop_domain: seeded.setdefault("shop", shop_domain),
    )

    with respx.mock(base_url=f"https://{shop}") as mock:
        mock.post("/admin/oauth/access_token").respond(
            status_code=200, json={"access_token": "token"}
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/shopify/auth/callback",
                params={
                    "shop": shop,
                    "code": "auth-code",
                    "state": state,
                    "hmac": "ok",
                },
            )

    assert response.status_code == 200
    assert seeded["shop"] == shop
