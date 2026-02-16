"""LLM model configuration CRUD routes."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app_context import AppContext, get_ctx

router = APIRouter()


class LLMConfigCreatePayload(BaseModel):
    shop_domain: str = Field(min_length=3)
    name: str = Field(min_length=1)
    provider: str = "ollama/openai-compat"
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


@router.get("/llm-configs", summary="List llm model configs for shop")
async def list_llm_configs(shop_domain: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    from application.use_cases.llm_configs.list_llm_configs import execute as list_llm_configs_execute

    configs = list_llm_configs_execute(supabase=ctx.services.supabase, shop_domain=shop_domain)
    return {"configs": configs}


@router.get("/llm-configs/active", summary="Get active llm model config for shop")
async def get_active_llm_config(shop_domain: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    from application.use_cases.llm_configs.get_active_llm_config import execute as get_active_execute
    config = get_active_execute(supabase=ctx.services.supabase, shop_domain=shop_domain)
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
            "api_key_masked": f"************{config.get('api_key_last4')}" if config.get("api_key_last4") else None,
            "created_at": config.get("created_at"),
            "updated_at": config.get("updated_at"),
        }
    }


@router.post("/llm-configs", summary="Create llm model config")
async def create_llm_config(payload: LLMConfigCreatePayload, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        from application.use_cases.llm_configs.create_llm_config import execute as create_llm_execute
        created = create_llm_execute(supabase=ctx.services.supabase, payload=payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"config": created}


@router.patch("/llm-configs/{config_id}", summary="Update llm model config")
async def update_llm_config(
    config_id: str, payload: LLMConfigUpdatePayload, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    try:
        from application.use_cases.llm_configs.update_llm_config import execute as update_llm_execute
        updated = update_llm_execute(supabase=ctx.services.supabase, config_id=config_id, payload=payload.model_dump(exclude_unset=True))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="Config not found")
    return {"config": updated}


@router.post("/llm-configs/{config_id}/activate", summary="Activate llm model config")
async def activate_llm_config(
    config_id: str, payload: ActivatePayload, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    from application.use_cases.llm_configs.activate_llm_config import execute as activate_llm_execute
    updated = activate_llm_execute(supabase=ctx.services.supabase, config_id=config_id, shop_domain=payload.shop_domain)
    if not updated:
        raise HTTPException(status_code=404, detail="Config not found")
    return {"config": updated}


@router.delete("/llm-configs/{config_id}", summary="Delete llm model config")
async def delete_llm_config(config_id: str, shop_domain: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    from application.use_cases.llm_configs.delete_llm_config import execute as delete_llm_execute
    if not delete_llm_execute(supabase=ctx.services.supabase, config_id=config_id, shop_domain=shop_domain):
        raise HTTPException(status_code=404, detail="Config not found")
    return {"status": "deleted", "id": config_id}
