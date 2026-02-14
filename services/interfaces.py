from typing import Any, AsyncIterator, Dict, Protocol, Union

from agent_framework import Workflow
from ai.models import ProductsList


class SupabaseServiceInterface(Protocol):
    def save_file(
        self, file_id: str, name: str, content: bytes, content_type: str | None = None
    ) -> None: ...

    def list_files(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]: ...

    def get_file(self, file_id: str) -> dict[str, Any] | None: ...

    def delete_file(self, file_id: str) -> bool: ...

    def create_or_update_run(self, run_id: str, fields: dict[str, Any]) -> None: ...

    def append_run_event(self, run_id: str, event: dict[str, Any], seq: int) -> None: ...

    def append_run_message(
        self,
        run_id: str,
        *,
        role: str,
        message: Any,
        seq: int,
        meta: dict[str, Any] | None = None,
    ) -> None: ...

    def finalize_run(
        self,
        run_id: str,
        *,
        status: str,
        duration_ms: int | None = None,
        error: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None: ...

    def list_runs(
        self, limit: int = 50, offset: int = 0, status: str | None = None
    ) -> list[dict[str, Any]]: ...

    def get_run(self, run_id: str) -> dict[str, Any] | None: ...

    def get_run_history(self, run_id: str) -> dict[str, Any]: ...

    def save_product_draft(
        self,
        *,
        draft_id: str,
        run_id: str | None,
        import_mode: str,
        draft_name: str | None,
        products: list[dict[str, Any]],
    ) -> dict[str, Any]: ...

    def list_product_drafts(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        sort_by: str = "date",
        sort_dir: str = "desc",
    ) -> list[dict[str, Any]]: ...

    def get_product_draft(self, draft_id: str) -> dict[str, Any] | None: ...

    def save_submitted_document(
        self,
        *,
        submitted_id: str,
        run_id: str | None,
        draft_id: str | None,
        name: str,
        import_mode: str,
        product_count: int,
        products: list[dict[str, Any]],
    ) -> dict[str, Any]: ...

    def list_submitted_documents(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        sort_by: str = "date",
        sort_dir: str = "desc",
    ) -> list[dict[str, Any]]: ...

    def get_submitted_document(self, submitted_id: str) -> dict[str, Any] | None: ...


class CollaboraServiceInterface(Protocol):
    async def convert_csv_to_excel(
        self,
        file_bytes: bytes,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> bytes: ...

    async def convert_excel_to_pdf_collabora(
        self,
        file_bytes: bytes,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> bytes: ...

    async def convert_pdf_to_png_collabora(
        self,
        pdf_bytes: bytes,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> list[bytes]: ...

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

    async def stream_run_events(self, run_id: str) -> AsyncIterator[str]: ...


class LLMServiceInterface(Protocol):
    def get_agent_workflow(
        self,
        excel_input: Union[bytes, str],
        collabora_base_url: str | None = None,
        agent_prompt: str = "Please analyze the spreadsheet and the associated image(s).",
        model_env: Dict[str, str] | None = None,
        *,
        write_to_file: bool = False,
        output_path: str | None = None,
        writer_agent_prompt: str | None = None,
        trace_event=None,
    ) -> Workflow: ...

    async def run_excel_agent_workflow(
        self,
        excel_input: Union[bytes, str],
        collabora_base_url: str | None = None,
        agent_prompt: str = "Please analyze the spreadsheet and the associated image(s).",
        model_env: Dict[str, str] | None = None,
        *,
        write_to_file: bool = False,
        output_path: str | None = None,
        writer_agent_prompt: str | None = None,
        trace_event=None,
    ) -> ProductsList | dict | str | None: ...
