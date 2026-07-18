from dataclasses import dataclass
from functools import lru_cache

from services import CollaboraService, LLMService, SupabaseService, TracingService

from application.ports.collabora_port import CollaboraPort
from application.ports.llm_port import LLMPort
from application.ports.supabase_port import SupabaseNamespacedPort
from application.ports.shopify_port import ShopifyPort
from application.ports.tracing_port import TracingPort
from application.ports.document_layout_port import DocumentLayoutPort
from infrastructure.adapters.shopify_adapter import ShopifyAdapter


@dataclass(frozen=True)
class ServiceRegistry:
    supabase: SupabaseNamespacedPort
    llm: LLMPort
    collabora: CollaboraPort
    tracing: TracingPort
    shopify: ShopifyPort
    document_layout: DocumentLayoutPort | None = None


@dataclass(frozen=True)
class AppContext:
    services: ServiceRegistry

    @property
    def supabase(self) -> SupabaseNamespacedPort:
        return self.services.supabase


@lru_cache(maxsize=1)
def get_app_context() -> AppContext:
    supabase_service = SupabaseService()
    from infrastructure.adapters.supabase_adapter import SupabaseAdapter

    supabase_adapter = SupabaseAdapter(supabase_service)
    collabora = CollaboraService()
    tracing = TracingService()
    llm = LLMService(collabora=collabora, supabase=supabase_adapter)
    shopify_adapter = ShopifyAdapter()
    import os
    from application.services.document_layout_service import DocumentLayoutService
    from infrastructure.adapters.azure_document_layout_adapter import AzureDocumentLayoutAdapter

    endpoint = os.getenv("DOCUMENTINTELLIGENCE_ENDPOINT", "").strip()
    key = os.getenv("DOCUMENTINTELLIGENCE_API_KEY", "").strip()
    if bool(endpoint) != bool(key):
        raise RuntimeError(
            "Azure Document Intelligence is partially configured; set both "
            "DOCUMENTINTELLIGENCE_ENDPOINT and DOCUMENTINTELLIGENCE_API_KEY"
        )
    azure = AzureDocumentLayoutAdapter(endpoint, key) if endpoint and key else None
    document_layout = DocumentLayoutService(azure)
    return AppContext(
        services=ServiceRegistry(
            supabase=supabase_adapter,
            llm=llm,
            collabora=collabora,
            tracing=tracing,
            shopify=shopify_adapter,
            document_layout=document_layout,
        )
    )


def get_ctx() -> AppContext:
    return get_app_context()
