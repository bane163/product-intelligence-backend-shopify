from typing import Any, AsyncIterator, Protocol

from ai.models import ProductsList
from application.ports.llm_port import ExtractionRequest
from application.ports.supabase_port import SupabaseNamespacedPort


SupabaseServiceInterface = SupabaseNamespacedPort


class CollaboraServiceInterface(Protocol):
    async def convert_csv_to_excel(
        self,
        file_bytes: bytes,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> bytes: ...

    async def convert_document_to_xlsx_collabora(
        self,
        file_bytes: bytes,
        *,
        filename: str,
        content_type: str,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> bytes: ...

    async def convert_excel_to_pdf_collabora(
        self,
        file_bytes: bytes,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> bytes: ...

    async def convert_document_to_pdf_collabora(
        self,
        file_bytes: bytes,
        *,
        filename: str,
        content_type: str,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> bytes: ...

    async def convert_pdf_to_png_collabora(
        self,
        pdf_bytes: bytes,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> list[bytes]: ...

    async def convert_document_to_png_collabora(
        self,
        file_bytes: bytes,
        *,
        filename: str,
        content_type: str,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> list[bytes]: ...

    async def extract_link_targets_collabora(
        self,
        file_bytes: bytes,
        *,
        filename: str,
        content_type: str,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> dict[str, dict[str, str]]: ...

    def get_runtime_url(self, default: str = "http://localhost:9980") -> str: ...

    def get_collabora_url_payload(self) -> dict[str, Any]: ...


class TracingServiceInterface(Protocol):
    def emit_run_event(
        self,
        run_id: str,
        *,
        phase: str,
        message: str,
        level: str = "info",
        payload_preview: Any = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def complete_run(self, run_id: str) -> None: ...

    def stream_run_events(self, run_id: str) -> AsyncIterator[str]: ...


class LLMServiceInterface(Protocol):
    async def extract(self, request: ExtractionRequest) -> ProductsList: ...
