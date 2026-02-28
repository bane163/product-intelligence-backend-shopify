import logging
import os
import uuid
from base64 import urlsafe_b64encode
from hashlib import sha256
from typing import Any, Optional

from cryptography.fernet import Fernet

LOG = logging.getLogger(__name__)


class SupabaseLlmConfigMixin:
    def _get_supabase_client(self) -> Optional[Any]:
        """Stub for typing — actual implementation provided by host class (e.g. SupabaseFileMixin)."""
        raise NotImplementedError(
            "_get_supabase_client must be implemented by the host class"
        )

    def _utc_now(self) -> str:
        """Stub for typing — actual implementation provided by `SupabaseRunsMixin`."""
        raise NotImplementedError("_utc_now must be provided by the host class")

    @staticmethod
    def _mask_api_key(raw_key: str | None) -> str | None:
        if not raw_key:
            return None
        if len(raw_key) <= 4:
            return "*" * len(raw_key)
        return f"{'*' * max(8, len(raw_key) - 4)}{raw_key[-4:]}"

    @staticmethod
    def _fernet_key_from_env(secret: str) -> bytes:
        if secret.startswith("gAAAA") or len(secret) == 44:
            try:
                return secret.encode("utf-8")
            except Exception as exc:
                raise RuntimeError("Invalid LLM_CONFIG_ENCRYPTION_KEY format") from exc
        digest = sha256(secret.encode("utf-8")).digest()
        return urlsafe_b64encode(digest)

    def _get_cipher(self) -> Fernet:
        raw = (
            os.getenv("LLM_CONFIG_ENCRYPTION_KEY", "").strip()
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
            or os.getenv("SUPABASE_SERVICE_KEY", "").strip()
        )
        if not raw:
            raise RuntimeError(
                "LLM config encryption requires LLM_CONFIG_ENCRYPTION_KEY (or SUPABASE service key env)"
            )
        return Fernet(self._fernet_key_from_env(raw))

    def _encrypt_api_key(self, api_key: str) -> str:
        if not api_key:
            return ""
        cipher = self._get_cipher()
        return cipher.encrypt(api_key.encode("utf-8")).decode("utf-8")

    def _decrypt_api_key(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        cipher = self._get_cipher()
        return cipher.decrypt(ciphertext.encode("utf-8")).decode("utf-8")

    def _sanitize_llm_model_config(self, row: dict[str, Any]) -> dict[str, Any]:
        masked = dict(row)
        masked.pop("api_key_ciphertext", None)
        masked["api_key_masked"] = self._mask_api_key(masked.pop("api_key", None)) or (
            f"************{masked.get('api_key_last4')}"
            if masked.get("api_key_last4")
            else None
        )
        return masked

    def list_llm_model_configs(self, shop_domain: str) -> list[dict[str, Any]]:
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("llm_model_configs")
                    .select("*")
                    .eq("shop_domain", shop_domain)
                    .order("created_at", desc=True)
                    .execute()
                )
                return [
                    self._sanitize_llm_model_config(item) for item in (res.data or [])
                ]
            except Exception:
                LOG.exception(
                    "Failed listing llm_model_configs for shop=%s", shop_domain
                )
        return [
            self._sanitize_llm_model_config(item)
            for item in self.llm_model_configs.values()
            if item.get("shop_domain") == shop_domain
        ]

    def create_llm_model_config(
        self,
        *,
        shop_domain: str,
        name: str,
        provider: str,
        base_url: str,
        model_id: str,
        api_key: str,
        version: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: int | None = None,
        is_active: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = self._utc_now()
        config_id = str(uuid.uuid4())
        payload = {
            "id": config_id,
            "shop_domain": shop_domain,
            "name": name,
            "provider": provider,
            "base_url": base_url,
            "model_id": model_id,
            "version": version,
            "api_key_ciphertext": self._encrypt_api_key(api_key),
            "api_key_last4": api_key[-4:] if api_key else None,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout_seconds": timeout_seconds,
            "is_active": bool(is_active),
            "extra": extra or {},
            "created_at": now,
            "updated_at": now,
        }
        client = self._get_supabase_client()
        if client:
            try:
                client.table("llm_model_configs").insert(payload).execute()
                if is_active:
                    activated = self.activate_llm_model_config(
                        config_id, shop_domain=shop_domain
                    )
                    if activated:
                        return activated
                return self._sanitize_llm_model_config(payload)
            except Exception:
                LOG.exception(
                    "Failed creating llm_model_config for shop=%s", shop_domain
                )

        if is_active:
            for item in self.llm_model_configs.values():
                if item.get("shop_domain") == shop_domain:
                    item["is_active"] = False
        self.llm_model_configs[config_id] = payload
        return self._sanitize_llm_model_config(payload)

    def update_llm_model_config(
        self,
        config_id: str,
        *,
        name: str | None = None,
        provider: str | None = None,
        base_url: str | None = None,
        model_id: str | None = None,
        api_key: str | None = None,
        version: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        update_fields: dict[str, Any] = {"updated_at": self._utc_now()}
        if name is not None:
            update_fields["name"] = name
        if provider is not None:
            update_fields["provider"] = provider
        if base_url is not None:
            update_fields["base_url"] = base_url
        if model_id is not None:
            update_fields["model_id"] = model_id
        if version is not None:
            update_fields["version"] = version
        if temperature is not None:
            update_fields["temperature"] = temperature
        if max_tokens is not None:
            update_fields["max_tokens"] = max_tokens
        if timeout_seconds is not None:
            update_fields["timeout_seconds"] = timeout_seconds
        if extra is not None:
            update_fields["extra"] = extra
        if api_key is not None:
            update_fields["api_key_ciphertext"] = self._encrypt_api_key(api_key)
            update_fields["api_key_last4"] = api_key[-4:] if api_key else None

        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("llm_model_configs")
                    .update(update_fields)
                    .eq("id", config_id)
                    .execute()
                )
                rows = res.data or []
                return self._sanitize_llm_model_config(rows[0]) if rows else None
            except Exception:
                LOG.exception("Failed updating llm_model_config=%s", config_id)

        config = self.llm_model_configs.get(config_id)
        if not config:
            return None
        config.update(update_fields)
        return self._sanitize_llm_model_config(config)

    def delete_llm_model_config(self, config_id: str, *, shop_domain: str) -> bool:
        deleted = False
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("llm_model_configs")
                    .delete()
                    .eq("id", config_id)
                    .eq("shop_domain", shop_domain)
                    .execute()
                )
                deleted = bool(res.data)
            except Exception:
                LOG.exception("Failed deleting llm_model_config=%s", config_id)
        config = self.llm_model_configs.get(config_id)
        if config and config.get("shop_domain") == shop_domain:
            del self.llm_model_configs[config_id]
            deleted = True
        return deleted

    def activate_llm_model_config(
        self, config_id: str, *, shop_domain: str
    ) -> dict[str, Any] | None:
        client = self._get_supabase_client()
        if client:
            try:
                client.table("llm_model_configs").update({"is_active": False}).eq(
                    "shop_domain", shop_domain
                ).execute()
                res = (
                    client.table("llm_model_configs")
                    .update({"is_active": True, "updated_at": self._utc_now()})
                    .eq("id", config_id)
                    .eq("shop_domain", shop_domain)
                    .execute()
                )
                rows = res.data or []
                return self._sanitize_llm_model_config(rows[0]) if rows else None
            except Exception:
                LOG.exception("Failed activating llm_model_config=%s", config_id)

        target = None
        for item in self.llm_model_configs.values():
            if item.get("shop_domain") == shop_domain:
                item["is_active"] = False
                if item.get("id") == config_id:
                    target = item
        if not target:
            return None
        target["is_active"] = True
        target["updated_at"] = self._utc_now()
        return self._sanitize_llm_model_config(target)

    def get_active_llm_model_config(self, shop_domain: str) -> dict[str, Any] | None:
        client = self._get_supabase_client()
        row: dict[str, Any] | None = None
        if client:
            try:
                res = (
                    client.table("llm_model_configs")
                    .select("*")
                    .eq("shop_domain", shop_domain)
                    .eq("is_active", True)
                    .limit(1)
                    .execute()
                )
                rows = res.data or []
                row = rows[0] if rows else None
            except Exception:
                LOG.exception(
                    "Failed fetching active llm_model_config for shop=%s", shop_domain
                )
        else:
            for item in self.llm_model_configs.values():
                if item.get("shop_domain") == shop_domain and item.get("is_active"):
                    row = item
                    break

        if not row:
            return None
        try:
            return {
                **row,
                "api_key": self._decrypt_api_key(row.get("api_key_ciphertext") or ""),
            }
        except Exception:
            LOG.exception(
                "Failed decrypting llm model config key for shop=%s", shop_domain
            )
            return None
