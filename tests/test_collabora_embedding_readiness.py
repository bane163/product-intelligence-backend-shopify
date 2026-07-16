from email.message import Message

import pytest

from services.collabora_service import CollaboraService, CollaboraUnavailable


DISCOVERY = b"""<wopi-discovery><net-zone><app name="calc"><action
    ext="xlsx" urlsrc="http://collabora:9980/browser/dist/cool.html?" />
</app></net-zone></wopi-discovery>"""


class FakeResponse:
    def __init__(self, body=b"", csp=None):
        self.body = body
        self.headers = Message()
        if csp is not None:
            self.headers["Content-Security-Policy"] = csp

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _responses(monkeypatch, csp):
    responses = iter([FakeResponse(DISCOVERY), FakeResponse(b"shell", csp)])
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setenv("COLLABORA_MIN_FREE_GIB", "0")
    monkeypatch.setenv("COLLABORA_MIN_FREE_PERCENT", "0")
    monkeypatch.setattr(CollaboraService, "get_runtime_url", staticmethod(lambda: "http://collabora:9980"))


@pytest.mark.parametrize(
    ("configured", "policy"),
    [
        ("*", "default-src 'none'; frame-ancestors *"),
        (
            "https://app.example.com https://admin.example.com",
            "default-src 'self'; frame-ancestors https://admin.example.com https://app.example.com; object-src 'none'",
        ),
    ],
)
def test_readiness_accepts_complete_embedding_policy(monkeypatch, configured, policy):
    monkeypatch.setenv("COLLABORA_FRAME_ANCESTORS", configured)
    _responses(monkeypatch, policy)

    result = CollaboraService().readiness()

    assert result["embedding_ready"] is True


@pytest.mark.parametrize(
    "policy",
    [
        "default-src 'self'",
        "frame-ancestors",
        "frame-ancestors https://app.example.com",
        "frame-src https://app.example.com",
    ],
)
def test_readiness_rejects_missing_or_incomplete_embedding_policy(monkeypatch, policy):
    monkeypatch.setenv(
        "COLLABORA_FRAME_ANCESTORS",
        "https://app.example.com https://admin.example.com",
    )
    _responses(monkeypatch, policy)

    with pytest.raises(CollaboraUnavailable) as error:
        CollaboraService().readiness()

    assert error.value.code == "COLLABORA_EMBEDDING_MISCONFIGURED"
    assert "app.example.com" not in str(error.value)
