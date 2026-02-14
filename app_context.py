from dataclasses import dataclass
from functools import lru_cache

from services import CollaboraService, LLMService, SupabaseService, TracingService


@dataclass(frozen=True)
class ServiceRegistry:
    supabase: SupabaseService
    llm: LLMService
    collabora: CollaboraService
    tracing: TracingService


@dataclass(frozen=True)
class AppContext:
    services: ServiceRegistry


@lru_cache(maxsize=1)
def get_app_context() -> AppContext:
    supabase = SupabaseService()
    collabora = CollaboraService()
    tracing = TracingService()
    llm = LLMService(collabora=collabora, supabase=supabase)
    return AppContext(
        services=ServiceRegistry(
            supabase=supabase,
            llm=llm,
            collabora=collabora,
            tracing=tracing,
        )
    )


def get_ctx() -> AppContext:
    return get_app_context()

