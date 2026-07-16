import logging
import os
from typing import Any
from dotenv import load_dotenv

load_dotenv()

LOG = logging.getLogger(__name__)


def _normalize_shop_domain(shop: str) -> str:
    return str(shop or "").strip().lower()


def _resolve_vault_secret(secret_name: str) -> str | None:
    normalized_name = str(secret_name or "").strip()
    if not normalized_name:
        return None

    try:
        from supabase_client import get_supabase

        client = get_supabase()
        response = client.rpc("get_vault_secret", {"p_name": normalized_name}).execute()
    except Exception:
        LOG.exception("Failed resolving vault secret name=%s", normalized_name)
        return None

    payload = getattr(response, "data", None)
    value: Any = payload
    if isinstance(payload, list):
        value = payload[0] if payload else None
    if isinstance(value, dict):
        value = value.get("get_vault_secret")
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _openai_vault_secret_names_for_shop(shop_domain: str) -> list[str]:
    base_name = str(os.getenv("OPENAI_VAULT_SECRET_BASENAME", "openai_api_key")).strip()
    if not base_name:
        base_name = "openai_api_key"
    tenant = _normalize_shop_domain(shop_domain)
    if not tenant:
        return [base_name]
    return [f"{base_name}__{tenant}", base_name]


def _resolve_openai_seed_key(shop_domain: str) -> tuple[str, str, str | None]:
    for secret_name in _openai_vault_secret_names_for_shop(shop_domain):
        secret = _resolve_vault_secret(secret_name)
        if secret:
            return secret, "vault", secret_name

    env_fallback = str(os.getenv("OPENAI_API_KEY", "")).strip()
    if env_fallback:
        return env_fallback, "env_fallback", "OPENAI_API_KEY"

    return "", "missing", None


def _seed_default_llm_configs_on_install(
    shop: str,
    *,
    include_ollama: bool = False,
    seed_source: str = "shopify_auth_install",
    openai_defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tenant = _normalize_shop_domain(shop)
    if not tenant:
        return {
            "created": 0,
            "skipped": 0,
            "openai_key_source": "missing",
            "openai_vault_secret_name": None,
        }

    from app_context import get_app_context

    llm_configs = get_app_context().supabase.llm_configs
    existing = llm_configs.list_llm_model_configs(tenant) or []
    has_ollama = any(
        "ollama" in str(item.get("provider") or "").strip().lower()
        for item in existing
    )
    has_openai = any(
        str(item.get("provider") or "").strip().lower().startswith("openai")
        for item in existing
    )
    has_active = any(bool(item.get("is_active")) for item in existing)

    defaults: list[dict[str, Any]] = []
    if include_ollama and not has_ollama:
        defaults.append(
            {
                "name": "Ollama Cloud (Default)",
                "provider": "ollama/openai-compat",
                "base_url": os.getenv("OLLAMA_CLOUD_URL", "http://localhost:11434/v1/"),
                "model_id": os.getenv("OLLAMA_MODEL_ID", "deepseek-r1:8b"),
                "extra": {
                    "seeded_by": seed_source,
                    "api_key_source": "env_ref",
                    "api_key_env_var": "OLLAMA_API_KEY",
                    "enable_file_search": False,
                },
            }
        )

    openai_seed = {
        "name": "OpenAI (Default)",
        "provider": "openai",
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "model_id": os.getenv("OPENAI_MODEL_ID", "gpt-5.4-mini"),
        "version": None,
        "temperature": None,
        "max_tokens": None,
        "timeout_seconds": None,
        "is_active": True,
        "enable_file_search": True,
    }
    if isinstance(openai_defaults, dict):
        openai_seed.update({k: v for k, v in openai_defaults.items() if v is not None})

    openai_key, openai_key_source, openai_key_ref = _resolve_openai_seed_key(tenant)
    if not has_openai:
        openai_extra = {
            "seeded_by": seed_source,
            "enable_file_search": bool(openai_seed.get("enable_file_search", True)),
            "api_key_source": openai_key_source,
        }
        if openai_key_source == "vault" and openai_key_ref:
            openai_extra["vault_secret_name"] = openai_key_ref
        elif openai_key_source == "env_fallback" and openai_key_ref:
            openai_extra["api_key_env_var"] = openai_key_ref

        defaults.append(
            {
                "name": str(openai_seed["name"]),
                "provider": "openai",
                "base_url": str(openai_seed["base_url"]),
                "model_id": str(openai_seed["model_id"]),
                "version": openai_seed.get("version"),
                "temperature": openai_seed.get("temperature"),
                "max_tokens": openai_seed.get("max_tokens"),
                "timeout_seconds": openai_seed.get("timeout_seconds"),
                "api_key": openai_key,
                "extra": openai_extra,
                "desired_active": bool(openai_seed.get("is_active", True)),
            }
        )

    created = 0
    for default in defaults:
        extra = default.get("extra") or {}
        env_var = str(extra.get("api_key_env_var") or "").strip()
        has_env_key = bool(env_var and os.getenv(env_var, "").strip())
        api_key = str(default.get("api_key") or "")
        desired_active = bool(default.get("desired_active", True))
        has_credential = bool(api_key) or has_env_key
        is_active = bool((not has_active) and desired_active and has_credential)
        if is_active:
            has_active = True
        try:
            llm_configs.create_llm_model_config(
                shop_domain=tenant,
                name=str(default["name"]),
                provider=str(default["provider"]),
                base_url=str(default["base_url"]),
                model_id=str(default["model_id"]),
                api_key=api_key,
                version=default.get("version"),
                temperature=default.get("temperature"),
                max_tokens=default.get("max_tokens"),
                timeout_seconds=default.get("timeout_seconds"),
                is_active=is_active,
                extra=dict(extra),
            )
            created += 1
        except Exception:
            LOG.exception(
                "Failed seeding default llm config provider=%s shop=%s",
                default.get("provider"),
                tenant,
            )

    skipped = 0
    if include_ollama and has_ollama:
        skipped += 1
    if has_openai:
        skipped += 1

    return {
        "created": created,
        "skipped": skipped,
        "openai_key_source": openai_key_source,
        "openai_vault_secret_name": openai_key_ref
        if openai_key_source == "vault"
        else None,
    }
