"""LLM model configuration CRUD routes."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
import hmac
import os
from .utils import require_authenticated_shop_domain
from pydantic import BaseModel, Field

import auth
from app_context import AppContext, get_ctx

def require_model_service(request: Request) -> str:
    expected = os.getenv("INTERNAL_SERVICE_KEY", "").strip()
    supplied = request.headers.get("x-stockpile-service-key", "").strip()
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="Invalid service authentication")
    if not request.headers.get("x-shop-domain", "").strip():
        raise HTTPException(status_code=400, detail="Missing authenticated shop_domain header")
    return require_authenticated_shop_domain(request)

router = APIRouter(dependencies=[Depends(require_model_service)])


class LLMConfigCreatePayload(BaseModel):
    shop_domain: str = Field(min_length=3)
    name: str = Field(min_length=1)
    provider: str = "openai"
    base_url: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    version: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: int | None = None
    is_active: bool = False
    extra: dict[str, Any] | None = None


class LLMConfigUpdatePayload(BaseModel):
    name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    model_id: str | None = None
    api_key: str | None = None
    version: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: int | None = None
    extra: dict[str, Any] | None = None


class ActivatePayload(BaseModel):
    shop_domain: str = Field(min_length=3)


class SeedDefaultsPayload(BaseModel):
    name: str = "OpenAI"
    provider: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    model_id: str = "gpt-6-luna"
    version: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: int | None = None
    enable_file_search: bool = False
    is_active: bool = True


class SeedPayload(BaseModel):
    shop_domain: str = Field(min_length=3)
    defaults: SeedDefaultsPayload | None = None


@router.get("/llm-configs", summary="List llm model configs for shop")
async def list_llm_configs(shop_domain: str, ctx: AppContext = Depends(get_ctx), tenant: str = Depends(require_model_service)) -> dict[str, Any]:
    from application.use_cases.llm_configs.list_llm_configs import execute as list_llm_configs_execute

    if shop_domain.lower() != tenant: raise HTTPException(status_code=403, detail="shop_domain mismatch")
    configs = list_llm_configs_execute(supabase=ctx.supabase, shop_domain=shop_domain)
    return {"configs": configs}


@router.get("/llm-configs/active", summary="Get active llm model config for shop")
async def get_active_llm_config(shop_domain: str, ctx: AppContext = Depends(get_ctx), tenant: str = Depends(require_model_service)) -> dict[str, Any]:
    from application.use_cases.llm_configs.get_active_llm_config import execute as get_active_execute
    if shop_domain.lower() != tenant: raise HTTPException(status_code=403, detail="shop_domain mismatch")
    config = get_active_execute(supabase=ctx.supabase, shop_domain=shop_domain)
    if not config:
        return {"config": None}
    return {
        "config": {
            "id": config.get("id"),
            "shop_domain": config.get("shop_domain"),
            "name": config.get("name"),
            "provider": config.get("provider"),
            "base_url": config.get("base_url"),
            "model_id": config.get("model_id"),
            "version": config.get("version"),
            "temperature": config.get("temperature"),
            "max_tokens": config.get("max_tokens"),
            "timeout_seconds": config.get("timeout_seconds"),
            "is_active": config.get("is_active"),
            "api_key_masked": config.get("api_key_masked")
            or (
                f"************{config.get('api_key_last4')}"
                if config.get("api_key_last4")
                else None
            ),
            "created_at": config.get("created_at"),
            "updated_at": config.get("updated_at"),
        }
    }


@router.post("/llm-configs", summary="Create llm model config")
async def create_llm_config(payload: LLMConfigCreatePayload, ctx: AppContext = Depends(get_ctx), tenant: str = Depends(require_model_service)) -> dict[str, Any]:
    if payload.shop_domain.lower() != tenant: raise HTTPException(status_code=403, detail="shop_domain mismatch")
    try:
        from application.use_cases.llm_configs.create_llm_config import execute as create_llm_execute
        created = create_llm_execute(supabase=ctx.supabase, payload=payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"config": created}


@router.post("/llm-configs/seed", summary="Seed default OpenAI llm model config")
async def seed_llm_config(payload: SeedPayload, tenant: str = Depends(require_model_service)) -> dict[str, Any]:
    if payload.shop_domain.lower() != tenant: raise HTTPException(status_code=403, detail="shop_domain mismatch")
    return auth._seed_default_llm_configs_on_install(
        payload.shop_domain,
        include_ollama=False,
        seed_source="llm_seed_endpoint",
        openai_defaults=(
            payload.defaults.model_dump(exclude_unset=True)
            if payload.defaults is not None
            else None
        ),
    )


@router.patch("/llm-configs/{config_id}", summary="Update llm model config")
async def update_llm_config(
    config_id: str, payload: LLMConfigUpdatePayload, ctx: AppContext = Depends(get_ctx), tenant: str = Depends(require_model_service)
) -> dict[str, Any]:
    try:
        from application.use_cases.llm_configs.update_llm_config import execute as update_llm_execute
        updated = update_llm_execute(supabase=ctx.supabase, config_id=config_id, payload=payload.model_dump(exclude_unset=True), shop_domain=tenant)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="Config not found")
    return {"config": updated}


@router.post("/llm-configs/{config_id}/activate", summary="Activate llm model config")
async def activate_llm_config(
    config_id: str, payload: ActivatePayload, ctx: AppContext = Depends(get_ctx), tenant: str = Depends(require_model_service)
) -> dict[str, Any]:
    if payload.shop_domain.lower() != tenant: raise HTTPException(status_code=403, detail="shop_domain mismatch")
    from application.use_cases.llm_configs.activate_llm_config import execute as activate_llm_execute
    updated = activate_llm_execute(supabase=ctx.supabase, config_id=config_id, shop_domain=payload.shop_domain)
    if not updated:
        raise HTTPException(status_code=404, detail="Config not found")
    return {"config": updated}


@router.delete("/llm-configs/{config_id}", summary="Delete llm model config")
async def delete_llm_config(config_id: str, shop_domain: str, ctx: AppContext = Depends(get_ctx), tenant: str = Depends(require_model_service)) -> dict[str, Any]:
    if shop_domain.lower() != tenant: raise HTTPException(status_code=403, detail="shop_domain mismatch")
    from application.use_cases.llm_configs.delete_llm_config import execute as delete_llm_execute
    if not delete_llm_execute(supabase=ctx.supabase, config_id=config_id, shop_domain=shop_domain):
        raise HTTPException(status_code=404, detail="Config not found")
    return {"status": "deleted", "id": config_id}
