from __future__ import annotations

from types import SimpleNamespace

import pytest

import ai.agent_client as agent_client


def _find_non_strict_object_nodes(node, path: str = "root") -> list[tuple[str, object]]:
    violations: list[tuple[str, object]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            additional_properties = node.get("additionalProperties", "__MISSING__")
            if additional_properties is not False:
                violations.append((path, additional_properties))
        for key, value in node.items():
            violations.extend(_find_non_strict_object_nodes(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            violations.extend(_find_non_strict_object_nodes(item, f"{path}[{index}]"))
    return violations


def _find_required_coverage_violations(node, path: str = "root") -> list[tuple[str, object]]:
    violations: list[tuple[str, object]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            properties = node.get("properties")
            required = node.get("required", "__MISSING__")
            if not isinstance(required, list):
                violations.append((path, "required_missing_or_not_array"))
            elif isinstance(properties, dict):
                missing = [key for key in properties.keys() if key not in required]
                if missing:
                    violations.append((path, missing))
        for key, value in node.items():
            violations.extend(_find_required_coverage_violations(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            violations.extend(_find_required_coverage_violations(item, f"{path}[{index}]"))
    return violations


@pytest.mark.asyncio
async def test_file_search_converts_spreadsheet_to_pdf_and_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple] = []

    class _FakeFilesAPI:
        async def create(self, *, file, purpose):
            name, content = file
            events.append(("files.create", name, content, purpose))
            return SimpleNamespace(id="file-1")

        async def delete(self, *, file_id: str):
            events.append(("files.delete", file_id))
            return SimpleNamespace(id=file_id, deleted=True)

    class _FakeVectorStoreFilesAPI:
        async def create_and_poll(self, *, vector_store_id: str, file_id: str):
            events.append(("vector_stores.files.create_and_poll", vector_store_id, file_id))
            return SimpleNamespace(status="completed", last_error=None)

    class _FakeVectorStoresAPI:
        def __init__(self) -> None:
            self.files = _FakeVectorStoreFilesAPI()

        async def create(self, **kwargs):
            events.append(("vector_stores.create", kwargs))
            return SimpleNamespace(id="vs-1")

        async def delete(self, *, vector_store_id: str):
            events.append(("vector_stores.delete", vector_store_id))
            return SimpleNamespace(id=vector_store_id, deleted=True)

    class _FakeResponsesAPI:
        async def create(self, **kwargs):
            events.append(("responses.create", kwargs))
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="message",
                        content=[SimpleNamespace(type="output_text", text='{"products": []}')],
                    )
                ],
                usage={"total_tokens": 11},
            )

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            events.append(("client.init", kwargs))
            self.files = _FakeFilesAPI()
            self.vector_stores = _FakeVectorStoresAPI()
            self.responses = _FakeResponsesAPI()

    async def fake_convert_document_to_pdf(
        file_bytes: bytes,
        *,
        filename: str,
        content_type: str,
        collabora_base_url: str,
    ) -> bytes:
        events.append(
            (
                "convert_document_to_pdf",
                file_bytes,
                filename,
                content_type,
                collabora_base_url,
            )
        )
        return b"%PDF-converted"

    monkeypatch.setattr(agent_client, "AsyncOpenAI", _FakeAsyncOpenAI)

    response = await agent_client.run_agent_on_source_with_file_search(
        source_bytes=b"xlsx-bytes",
        source_filename="catalog.xlsx",
        source_content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        document_kind="spreadsheet",
        agent_prompt="Extract products from this catalog",
        model_env={
            "OLLAMA_API_KEY": "test-key",
            "OLLAMA_MODEL_ID": "gpt-4.1-mini",
            "OLLAMA_CLOUD_URL": "https://api.openai.com/v1",
        },
        convert_document_to_pdf=fake_convert_document_to_pdf,
        collabora_base_url="http://collabora:9980",
    )

    assert ("convert_document_to_pdf", b"xlsx-bytes", "catalog.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "http://collabora:9980") in events
    assert ("files.create", "catalog.pdf", b"%PDF-converted", "user_data") in events
    assert ("vector_stores.files.create_and_poll", "vs-1", "file-1") in events
    assert ("vector_stores.delete", "vs-1") in events
    assert ("files.delete", "file-1") in events

    response_calls = [entry for entry in events if entry[0] == "responses.create"]
    assert response_calls, "Expected an OpenAI responses.create call"
    tools = response_calls[0][1]["tools"]
    assert isinstance(tools, list) and tools and tools[0]["type"] == "file_search"
    assert tools[0]["vector_store_ids"] == ["vs-1"]
    schema = response_calls[0][1]["text"]["format"]["schema"]
    assert _find_non_strict_object_nodes(schema) == []
    assert _find_required_coverage_violations(schema) == []
    assert response.value is not None
