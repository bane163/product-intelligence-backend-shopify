import os
import shutil
import urllib.request
import xml.etree.ElementTree as ET

from ai import collabora_utils
from .interfaces import CollaboraServiceInterface


class CollaboraUnavailable(RuntimeError):
    def __init__(self, message: str, code: str = "COLLABORA_UNAVAILABLE"):
        super().__init__(message)
        self.code = code


class CollaboraService(CollaboraServiceInterface):
    def readiness(self) -> dict:
        path = os.getenv("COLLABORA_DISK_PATH", "/tmp")
        usage = shutil.disk_usage(path)
        free_gib = usage.free / (1024 ** 3)
        free_percent = usage.free * 100 / max(usage.total, 1)
        min_gib = float(os.getenv("COLLABORA_MIN_FREE_GIB", "10"))
        min_percent = float(os.getenv("COLLABORA_MIN_FREE_PERCENT", "10"))
        if free_gib < min_gib or free_percent < min_percent:
            raise CollaboraUnavailable(
                f"Document preview is unavailable because storage is low ({free_gib:.1f} GiB, {free_percent:.1f}% free).",
                "COLLABORA_STORAGE_LOW",
            )
        url = self.get_runtime_url().rstrip("/") + "/hosting/discovery"
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                discovery = response.read()
            if not discovery:
                raise ValueError("empty discovery response")
        except CollaboraUnavailable:
            raise
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            raise CollaboraUnavailable(
                f"The document viewer is temporarily unavailable ({message})."
            ) from exc
        return {"status": "ready", "free_gib": round(free_gib, 2), "free_percent": round(free_percent, 2), "discovery": discovery}

    @staticmethod
    def _viewer_url_base(discovery: bytes, browser_url: str) -> str:
        try:
            root = ET.fromstring(discovery)
            action = next((node for node in root.iter("action") if node.attrib.get("urlsrc")), None)
            source = action.attrib["urlsrc"] if action is not None else ""
            marker = source.find("/browser/")
            suffix = source[marker:].split("?", 1)[0] if marker >= 0 else "/browser/dist/cool.html"
            return browser_url.rstrip("/") + suffix
        except Exception:
            return browser_url.rstrip("/") + "/browser/dist/cool.html"
    async def convert_csv_to_excel(
        self,
        file_bytes: bytes,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> bytes:
        return await collabora_utils.convert_csv_to_excel(
            file_bytes, collabora_base_url=collabora_base_url, timeout=timeout
        )

    async def convert_document_to_xlsx_collabora(
        self,
        file_bytes: bytes,
        *,
        filename: str,
        content_type: str,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> bytes:
        return await collabora_utils.convert_document_to_xlsx_collabora(
            file_bytes,
            filename=filename,
            content_type=content_type,
            collabora_base_url=collabora_base_url,
            timeout=timeout,
        )

    async def convert_excel_to_pdf_collabora(
        self,
        file_bytes: bytes,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> bytes:
        return await collabora_utils.convert_excel_to_pdf_collabora(
            file_bytes, collabora_base_url=collabora_base_url, timeout=timeout
        )

    async def convert_document_to_pdf_collabora(
        self,
        file_bytes: bytes,
        *,
        filename: str,
        content_type: str,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> bytes:
        return await collabora_utils.convert_document_to_pdf_collabora(
            file_bytes,
            filename=filename,
            content_type=content_type,
            collabora_base_url=collabora_base_url,
            timeout=timeout,
        )

    async def convert_pdf_to_png_collabora(
        self,
        pdf_bytes: bytes,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> list[bytes]:
        return await collabora_utils.convert_pdf_to_png_collabora(
            pdf_bytes, collabora_base_url=collabora_base_url, timeout=timeout
        )

    async def convert_document_to_png_collabora(
        self,
        file_bytes: bytes,
        *,
        filename: str,
        content_type: str,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> list[bytes]:
        return await collabora_utils.convert_document_to_png_collabora(
            file_bytes,
            filename=filename,
            content_type=content_type,
            collabora_base_url=collabora_base_url,
            timeout=timeout,
        )

    async def extract_link_targets_collabora(
        self,
        file_bytes: bytes,
        *,
        filename: str,
        content_type: str,
        collabora_base_url: str = "http://localhost:8080",
        timeout: int = 60,
    ) -> dict[str, dict[str, str]]:
        return await collabora_utils.extract_link_targets_collabora(
            file_bytes,
            filename=filename,
            content_type=content_type,
            collabora_base_url=collabora_base_url,
            timeout=timeout,
        )

    @staticmethod
    def get_runtime_url(default: str = "http://localhost:9980") -> str:
        from cloudflare_tunnel import get_tunnel_url

        return get_tunnel_url() or os.getenv("COLLABORA_URL", default)

    def get_collabora_url_payload(self) -> dict:
        from cloudflare_tunnel import get_tunnel_url

        tunnel_url = get_tunnel_url()
        public_url = os.getenv("COLLABORA_PUBLIC_URL", "").strip()
        browser_url = tunnel_url or public_url
        if not browser_url:
            raise RuntimeError(
                "The Collabora viewer is not available yet. Wait for the local HTTPS tunnel to start and retry."
            )
        try:
            import ipaddress
            from urllib.parse import urlparse

            parsed_url = urlparse(browser_url)
        except ValueError:
            parsed_url = None
        hostname = parsed_url.hostname if parsed_url else None
        unsafe_hostname = not hostname or hostname == "localhost" or "." not in hostname
        if hostname:
            try:
                unsafe_hostname = not ipaddress.ip_address(hostname).is_global
            except ValueError:
                pass
        if not parsed_url or parsed_url.scheme != "https" or unsafe_hostname:
            raise RuntimeError(
                "The browser-facing Collabora URL must be a public HTTPS URL. "
                "Configure COLLABORA_PUBLIC_URL or enable the local tunnel."
            )
        wopi_host = os.getenv("WOPI_HOST", "shopify-backend")
        wopi_port = os.getenv("WOPI_PORT", "8000")
        try:
            readiness = self.readiness()
            discovery = readiness["discovery"]
        except CollaboraUnavailable:
            discovery = b""
        return {
            "collabora_url": browser_url.rstrip("/"),
            "wopi_base_url": f"http://{wopi_host}:{wopi_port}/agents/wopi/files",
            "is_tunnel": tunnel_url is not None,
            "viewer_url_base": self._viewer_url_base(discovery, browser_url),
        }
