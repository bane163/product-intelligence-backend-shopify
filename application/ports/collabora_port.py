from typing import Any, Protocol


class CollaboraPort(Protocol):
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

    def get_runtime_url(self, default: str = "http://localhost:9980") -> str: ...

    def get_collabora_url_payload(self) -> dict[str, Any]: ...
