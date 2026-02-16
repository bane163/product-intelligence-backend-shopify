"""Use-case: convert CSV bytes to Excel via collabora port."""
from services.interfaces import CollaboraServiceInterface


async def execute(collabora: CollaboraServiceInterface, csv_bytes: bytes, collabora_base_url: str | None = None) -> bytes:
    return await collabora.convert_csv_to_excel(csv_bytes, collabora_base_url=collabora_base_url)
