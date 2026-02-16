"""Use-case: return collabora URL payload via collabora port."""
from application.ports.collabora_port import CollaboraPort


def execute(collabora: CollaboraPort) -> dict[str, object]:
    return collabora.get_collabora_url_payload()
