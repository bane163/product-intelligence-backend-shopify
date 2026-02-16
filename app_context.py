from dataclasses import dataclass
from functools import lru_cache

from services import CollaboraService, LLMService, SupabaseService, TracingService

from application.ports.collabora_port import CollaboraPort
from application.ports.llm_port import LLMPort
from application.ports.supabase_port import SupabasePort
from application.ports.shopify_port import ShopifyPort
from application.ports.tracing_port import TracingPort
from infrastructure.adapters.shopify_adapter import ShopifyAdapter


@dataclass(frozen=True)
class ServiceRegistry:
    supabase: SupabasePort
    llm: LLMPort
    collabora: CollaboraPort
    tracing: TracingPort
    shopify: ShopifyPort


@dataclass(frozen=True)
class AppContext:
    services: ServiceRegistry


@lru_cache(maxsize=1)
def get_app_context() -> AppContext:
    supabase_service = SupabaseService()
    from infrastructure.adapters.supabase_adapter import SupabaseAdapter

    supabase_adapter = SupabaseAdapter(supabase_service)
    collabora = CollaboraService()
    tracing = TracingService()
    llm = LLMService(collabora=collabora, supabase=supabase_adapter)
    shopify_adapter = ShopifyAdapter()
    return AppContext(
        services=ServiceRegistry(
            supabase=supabase_adapter,
            llm=llm,
            collabora=collabora,
            tracing=tracing,
            shopify=shopify_adapter,
        )
    )


def get_ctx() -> AppContext:
    return get_app_context()
