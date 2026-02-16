"""Use-case: convert CSV bytes to Excel via collabora port."""
from application.ports.collabora_port import CollaboraPort


async def execute(collabora: CollaboraPort, csv_bytes: bytes, collabora_base_url: str | None = None) -> bytes:
    return await collabora.convert_csv_to_excel(csv_bytes, collabora_base_url=collabora_base_url)
