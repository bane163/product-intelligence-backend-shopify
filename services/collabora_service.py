import os

from ai import collabora_utils
from .interfaces import CollaboraServiceInterface


class CollaboraService(CollaboraServiceInterface):
    async def convert_excel_to_pdf_collabora(
        self,
        file_bytes: bytes,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> bytes:
        _ = timeout
        return await collabora_utils.convert_excel_to_pdf_collabora(
            file_bytes, collabora_base_url=collabora_base_url
        )

    async def convert_pdf_to_png_collabora(
        self,
        pdf_bytes: bytes,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> list[bytes]:
        _ = timeout
        return await collabora_utils.convert_pdf_to_png_collabora(
            pdf_bytes, collabora_base_url=collabora_base_url
        )

    @staticmethod
    def get_runtime_url(default: str = "http://localhost:9980") -> str:
        from cloudflare_tunnel import get_tunnel_url

        return get_tunnel_url() or os.getenv("COLLABORA_URL", default)

    def get_collabora_url_payload(self) -> dict:
        from cloudflare_tunnel import get_tunnel_url

        tunnel_url = get_tunnel_url()
        fallback_url = os.getenv("COLLABORA_URL", "http://localhost:9980")
        wopi_host = os.getenv("WOPI_HOST", "shopify-backend")
        wopi_port = os.getenv("WOPI_PORT", "8000")
        return {
            "collabora_url": tunnel_url or fallback_url,
            "wopi_base_url": f"http://{wopi_host}:{wopi_port}/agents/wopi/files",
            "is_tunnel": tunnel_url is not None,
        }
