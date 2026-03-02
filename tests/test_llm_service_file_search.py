from __future__ import annotations

from types import SimpleNamespace

import pytest

import ai.agent_collector as agent_collector_module
import services.llm_service as llm_service_module
from services.llm_service import LLMService, _is_openai_file_search_enabled


class _FakeCollabora:
    async def convert_document_to_xlsx_collabora(self, *args, **kwargs):
        _ = args, kwargs
        return b""

    async def convert_document_to_png_collabora(self, *args, **kwargs):
        _ = args, kwargs
        return []

    async def convert_document_to_pdf_collabora(self, *args, **kwargs):
        _ = args, kwargs
        return b"%PDF"


class _FakeSupabase:
    pass


def test_is_openai_file_search_enabled_prefers_openai_base_url_over_provider() -> None:
    assert (
        _is_openai_file_search_enabled(
            model_provider="ollama/openai-compat",
            model_env={"OLLAMA_CLOUD_URL": "https://api.openai.com/v1"},
        )
        is True
    )


def test_is_openai_file_search_enabled_prefers_non_openai_base_url_over_provider() -> None:
    assert (
        _is_openai_file_search_enabled(
            model_provider="openai",
            model_env={"OLLAMA_CLOUD_URL": "http://localhost:11434/v1"},
        )
        is False
    )


def test_is_openai_file_search_enabled_uses_provider_when_base_url_missing() -> None:
    assert (
        _is_openai_file_search_enabled(
            model_provider="openai",
            model_env={},
        )
        is True
    )


def test_is_openai_file_search_enabled_honors_explicit_disable() -> None:
    assert (
        _is_openai_file_search_enabled(
            model_provider="openai",
            model_env={"OLLAMA_CLOUD_URL": "https://api.openai.com/v1"},
            model_file_search_enabled=False,
        )
        is False
    )


@pytest.mark.asyncio
async def test_llm_service_uses_file_search_for_openai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"file_search": 0, "legacy": 0}
    expected_input = b"title\nShirt"

    async def fake_file_search(**kwargs):
        calls["file_search"] += 1
        assert kwargs["source_bytes"] == expected_input
        return SimpleNamespace(
            value={"products": [{"title": "Shirt"}]},
            text='{"products":[{"title":"Shirt"}]}',
        )

    async def fake_legacy(*args, **kwargs):
        _ = args, kwargs
        calls["legacy"] += 1
        return SimpleNamespace(
            value={"products": [{"title": "Legacy"}]},
            text='{"products":[{"title":"Legacy"}]}',
        )

    monkeypatch.setattr(
        agent_collector_module,
        "run_agent_on_source_with_file_search",
        fake_file_search,
    )
    monkeypatch.setattr(agent_collector_module, "run_agent_on_inputs", fake_legacy)

    service = LLMService(collabora=_FakeCollabora(), supabase=_FakeSupabase())
    result = await service.run_excel_agent_workflow(
        expected_input,
        input_name="catalog.csv",
        input_content_type="text/csv",
        model_provider="openai",
        model_env={
            "OLLAMA_API_KEY": "test-key",
            "OLLAMA_MODEL_ID": "gpt-4.1-mini",
            "OLLAMA_CLOUD_URL": "https://api.openai.com/v1",
        },
    )

    assert calls["file_search"] == 1
    assert calls["legacy"] == 0
    assert result is not None
    assert result.products[0].title == "Shirt"


@pytest.mark.asyncio
async def test_llm_service_uses_legacy_path_for_ollama_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"file_search": 0, "legacy": 0}

    async def fake_file_search(**kwargs):
        _ = kwargs
        calls["file_search"] += 1
        return SimpleNamespace(
            value={"products": [{"title": "Shirt"}]},
            text='{"products":[{"title":"Shirt"}]}',
        )

    async def fake_legacy(*args, **kwargs):
        _ = args, kwargs
        calls["legacy"] += 1
        return SimpleNamespace(
            value={"products": [{"title": "Legacy"}]},
            text='{"products":[{"title":"Legacy"}]}',
        )

    monkeypatch.setattr(
        agent_collector_module,
        "run_agent_on_source_with_file_search",
        fake_file_search,
    )
    monkeypatch.setattr(agent_collector_module, "run_agent_on_inputs", fake_legacy)

    service = LLMService(collabora=_FakeCollabora(), supabase=_FakeSupabase())
    result = await service.run_excel_agent_workflow(
        b"title\nLegacy",
        input_name="catalog.csv",
        input_content_type="text/csv",
        model_provider="ollama/openai-compat",
        model_env={
            "OLLAMA_API_KEY": "test-key",
            "OLLAMA_MODEL_ID": "deepseek-r1:8b",
            "OLLAMA_CLOUD_URL": "http://localhost:11434/v1/",
        },
    )

    assert calls["file_search"] == 0
    assert calls["legacy"] == 1
    assert result is not None
    assert result.products[0].title == "Legacy"


@pytest.mark.asyncio
async def test_llm_service_uses_legacy_path_when_file_search_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"file_search": 0, "legacy": 0}

    async def fake_file_search(**kwargs):
        _ = kwargs
        calls["file_search"] += 1
        return SimpleNamespace(
            value={"products": [{"title": "Search"}]},
            text='{"products":[{"title":"Search"}]}',
        )

    async def fake_legacy(*args, **kwargs):
        _ = args, kwargs
        calls["legacy"] += 1
        return SimpleNamespace(
            value={"products": [{"title": "Legacy"}]},
            text='{"products":[{"title":"Legacy"}]}',
        )

    monkeypatch.setattr(
        agent_collector_module,
        "run_agent_on_source_with_file_search",
        fake_file_search,
    )
    monkeypatch.setattr(agent_collector_module, "run_agent_on_inputs", fake_legacy)

    service = LLMService(collabora=_FakeCollabora(), supabase=_FakeSupabase())
    result = await service.run_excel_agent_workflow(
        b"title\nLegacy",
        input_name="catalog.csv",
        input_content_type="text/csv",
        model_provider="openai",
        model_env={
            "OLLAMA_API_KEY": "test-key",
            "OLLAMA_MODEL_ID": "gpt-4.1-mini",
            "OLLAMA_CLOUD_URL": "https://api.openai.com/v1",
        },
        model_file_search_enabled=False,
    )

    assert calls["file_search"] == 0
    assert calls["legacy"] == 1
    assert result is not None
    assert result.products[0].title == "Legacy"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        (
            "catalog.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        ("catalog.xls", "application/vnd.ms-excel"),
    ],
)
async def test_llm_service_uses_legacy_path_for_openai_excel_inputs(
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    content_type: str,
) -> None:
    calls = {"file_search": 0, "legacy": 0}

    async def fake_file_search(**kwargs):
        _ = kwargs
        calls["file_search"] += 1
        return SimpleNamespace(
            value={"products": [{"title": "Search"}]},
            text='{"products":[{"title":"Search"}]}',
        )

    async def fake_legacy(*args, **kwargs):
        _ = args, kwargs
        calls["legacy"] += 1
        return SimpleNamespace(
            value={"products": [{"title": "Legacy"}]},
            text='{"products":[{"title":"Legacy"}]}',
        )

    monkeypatch.setattr(
        agent_collector_module,
        "run_agent_on_source_with_file_search",
        fake_file_search,
    )
    monkeypatch.setattr(agent_collector_module, "run_agent_on_inputs", fake_legacy)
    monkeypatch.setattr(
        llm_service_module,
        "extract_excel_contents",
        lambda *args, **kwargs: "Sheet\nLegacy",
    )

    service = LLMService(collabora=_FakeCollabora(), supabase=_FakeSupabase())
    result = await service.run_excel_agent_workflow(
        b"not-a-real-spreadsheet",
        input_name=filename,
        input_content_type=content_type,
        model_provider="openai",
        model_env={
            "OLLAMA_API_KEY": "test-key",
            "OLLAMA_MODEL_ID": "gpt-4.1-mini",
            "OLLAMA_CLOUD_URL": "https://api.openai.com/v1",
        },
    )

    assert calls["file_search"] == 0
    assert calls["legacy"] == 1
    assert result is not None
    assert result.products[0].title == "Legacy"
