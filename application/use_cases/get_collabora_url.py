"""Use-case: return collabora URL payload via collabora port."""
from services.interfaces import CollaboraServiceInterface


def execute(collabora: CollaboraServiceInterface) -> dict[str, object]:
    return collabora.get_collabora_url_payload()
