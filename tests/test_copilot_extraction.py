from __future__ import annotations

import base64
from io import BytesIO
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from copilot.generated.session_events import (
    PermissionRequestCustomTool,
    PermissionRequestRead,
)
from copilot.tools import Tool, ToolInvocation

from application.ports.llm_port import ExtractionRequest, ModelConfiguration
from application.services.pdf_evidence_index import EvidenceCatalog, EvidenceEntry
from application.services.pdf_evidence_index import validate_indexed_pdf_evidence
from services.llm_service import (
    MAX_PREVIEW_PAGES,
    MAX_RANGE_CELLS,
    MONTY_RESOURCE_LIMITS,
    LLMService,
    PagePreviewTool,
    RunScopedExtractionTools,
    map_copilot_provider,
    resolve_collabora_url,
    sanitize_source_filename,
    scoped_permission_handler,
)


def model(
    *,
    key: str = "super-secret-key",
    provider: str = "openai",
    base_url: str = "https://api.openai.com/v1",
):
    return ModelConfiguration(
        provider=provider,
        base_url=base_url,
        model_id="gpt-test",
        api_key=key,
        max_tokens=1234,
    )


def request(**overrides):
    values = {
        "document_kind": "csv",
        "content": b"Title,SKU\nOne,A-1\n",
        "filename": "catalog.csv",
        "content_type": "text/csv",
        "model": model(),
    }
    values.update(overrides)
    return ExtractionRequest(**values)


def test_byok_provider_mapping_and_auth_requirements():
    mapped = map_copilot_provider(model())
    assert mapped == {
        "type": "openai",
        "wire_api": "responses",
        "base_url": "https://api.openai.com/v1",
        "api_key": "super-secret-key",
        "model_id": "gpt-test",
        "wire_model": "gpt-test",
        "max_output_tokens": 1234,
    }
    compatible = map_copilot_provider(
        model(provider="ollama/openai-compat", base_url="http://ollama:11434/v1/")
    )
    assert compatible["wire_api"] == "completions"
    assert compatible["base_url"] == "http://ollama:11434/v1"
    with pytest.raises(RuntimeError, match="AUTH_REQUIRED"):
        map_copilot_provider(model(key=""))
    with pytest.raises(RuntimeError, match="PROVIDER_UNSUPPORTED"):
        map_copilot_provider(model(provider="anthropic"))


def test_pdf_prompt_requires_product_relative_evidence_paths():
    prompt = LLMService._prompt(
        request(
            document_kind="pdf",
            content=b"%PDF",
            filename="catalog.pdf",
            content_type="application/pdf",
        )
    )
    assert "product-relative" in prompt
    assert "must never start with products[<index>]" in prompt
    assert "variant[<variant SKU or title>].sku or .price" in prompt


def test_scoped_permission_handler_only_approves_registered_custom_tools():
    extraction_handler = scoped_permission_handler({"execute_code"})
    execute = PermissionRequestCustomTool(
        tool_description="Monty", tool_name="execute_code"
    )
    preview = PermissionRequestCustomTool(
        tool_description="Preview", tool_name="view_page"
    )
    unknown = PermissionRequestCustomTool(
        tool_description="Unknown", tool_name="other_tool"
    )
    read = PermissionRequestRead(intention="inspect", path="/etc/passwd")

    assert extraction_handler(execute, {}).kind == "approve-once"
    assert extraction_handler(preview, {}).kind == "user-not-available"
    assert extraction_handler(unknown, {}).kind == "user-not-available"
    assert extraction_handler(read, {}).kind == "user-not-available"

    preview_handler = scoped_permission_handler({"execute_code", "view_page"})
    assert preview_handler(preview, {}).kind == "approve-once"


@pytest.mark.parametrize(
    ("unsafe", "safe"),
    [
        ("../../secrets.csv", "secrets.csv"),
        (r"..\\..\\payload.xlsx", "payload.xlsx"),
        (".github/copilot-instructions.md", "copilot-instructions.md"),
        ("...", "source.bin"),
    ],
)
def test_source_filename_is_sanitized_to_a_plain_basename(unsafe, safe):
    assert sanitize_source_filename(unsafe) == safe


def test_monty_is_resource_bounded_without_host_mounts():
    tools = RunScopedExtractionTools(request())
    from agent_framework_monty import MontyCodeActProvider

    provider = MontyCodeActProvider(
        tools=[tools.document_manifest], resource_limits=MONTY_RESOURCE_LIMITS
    )
    state = provider._execute_code_tool.build_serializable_state()
    assert state["workspace_root"] is None
    assert state["file_mounts"] == []
    assert state["resource_limits"] == {
        "max_duration_secs": 30.0,
        "max_memory": 128 * 1024 * 1024,
    }


@pytest.mark.asyncio
async def test_pinned_monty_runtime_reads_evidence_and_submits_extraction():
    scoped = RunScopedExtractionTools(request())
    from agent_framework_monty import MontyCodeActProvider

    provider = MontyCodeActProvider(
        tools=[scoped.read_workbook_range, scoped.submit_extraction],
        resource_limits=MONTY_RESOURCE_LIMITS,
    )
    output = await provider._execute_code_tool.invoke(
        arguments={
            "code": (
                "rows = await read_workbook_range(sheet='Sheet1', start_row=1, end_row=2, "
                "start_column=1, end_column=2)\n"
                "await submit_extraction(payload={'products': "
                "[{'title': rows['rows'][1][0]['value']}]})"
            )
        }
    )

    assert scoped.submitted is not None
    assert scoped.submitted.products[0].title == "One"
    assert not any(item.type == "error" for item in output)


def test_model_supplied_pdf_locators_are_rejected_even_with_valid_index():
    catalog = EvidenceCatalog(
        (EvidenceEntry(1, "Demo", 2, (0.1, 0.2, 0.3, 0.4), "azure-1", "azure"),)
    )
    product = {
        "title": "Demo",
        "source_refs": [
            {
                "value_path": "title",
                "evidence_index": 1,
                "page": 99,
                "bbox": [0, 0, 1, 1],
            }
        ],
    }
    result = validate_indexed_pdf_evidence([product], catalog)
    assert not result.valid
    assert "model-supplied locators" in result.feedback


@pytest.mark.asyncio
async def test_paginated_large_evidence_and_large_csv_ranges():
    catalog = EvidenceCatalog(
        tuple(
            EvidenceEntry(
                i, f"item {i}", (i - 1) // 20 + 1, (0, 0, 1, 1), f"a{i}", "azure"
            )
            for i in range(1, 451)
        )
    )
    evidence_tools = RunScopedExtractionTools(
        request(
            document_kind="pdf",
            content=b"%PDF",
            filename="x.pdf",
            content_type="application/pdf",
            evidence_catalog=catalog,
        )
    )
    third = await evidence_tools.read_evidence_page(page=3, page_size=200)
    assert third["total"] == 450
    assert third["items"][0]["evidence_index"] == 401
    matches = await evidence_tools.search_evidence("item 44", page_size=50)
    assert matches["total"] >= 2

    csv_bytes = (
        "Title,SKU\n" + "\n".join(f"Product {i},SKU-{i}" for i in range(1, 701))
    ).encode()
    csv_tools = RunScopedExtractionTools(request(content=csv_bytes))
    result = await csv_tools.read_workbook_range("Sheet1", 650, 700, 1, 2)
    assert result["range"] == "A650:B700"
    assert result["rows"][-1][1] == {"cell": "B700", "value": "SKU-699"}
    with pytest.raises(ValueError, match=str(MAX_RANGE_CELLS)):
        await csv_tools.read_workbook_range("Sheet1", 1, 101, 1, 20)


@pytest.mark.asyncio
async def test_large_workbook_ranges_keep_deterministic_cell_references():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Catalog"
    for row in range(1, 401):
        sheet.append([f"Product {row}", f"SKU-{row}"])
    buffer = BytesIO()
    workbook.save(buffer)
    tools = RunScopedExtractionTools(
        request(
            document_kind="spreadsheet",
            content=buffer.getvalue(),
            filename="catalog.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    )
    result = await tools.read_workbook_range("Catalog", 350, 351, 1, 2)
    assert result["rows"][0] == [
        {"cell": "A350", "value": "Product 350"},
        {"cell": "B350", "value": "SKU-350"},
    ]


@pytest.mark.asyncio
async def test_duplicate_submission_is_rejected():
    tools = RunScopedExtractionTools(request())
    await tools.submit_extraction({"products": [{"title": "First"}]})
    with pytest.raises(RuntimeError, match="already submitted"):
        await tools.submit_extraction({"products": [{"title": "Second"}]})
    assert tools.submitted.products[0].title == "First"


def test_collabora_url_precedence_and_sanitized_source_trace(monkeypatch):
    monkeypatch.setenv("COLLABORA_URL", "http://worker-secret:9980")
    explicit = request(collabora_base_url="http://job-secret:9980")
    assert resolve_collabora_url(explicit) == ("http://job-secret:9980", "job")
    assert resolve_collabora_url(request()) == (
        "http://worker-secret:9980",
        "worker_env",
    )
    monkeypatch.delenv("COLLABORA_URL")
    assert resolve_collabora_url(request()) == (
        "http://localhost:8080",
        "local_default",
    )


@pytest.mark.asyncio
async def test_preview_conversion_failure_is_latched_and_sanitized(tmp_path):
    calls = []
    traces = []

    class FailingCollabora:
        async def convert_document_to_pdf_collabora(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise ConnectionError("http://secret-collabora:9980/private")

    tool = PagePreviewTool(
        request(
            document_kind="spreadsheet", content=b"not-needed", filename="catalog.xlsx"
        ),
        FailingCollabora(),
        tmp_path,
        collabora_url="http://secret-collabora:9980",
        trace_event=lambda **event: traces.append(event),
    )
    first = await tool.view_page(1)
    second = await tool.view_page(2)
    assert len(calls) == 1
    assert "disabled" in first.text_result_for_llm
    assert "unavailable" in second.text_result_for_llm
    assert {event["phase"] for event in traces} == {
        "copilot_preview_unavailable",
        "copilot_preview_fallback",
    }
    assert "secret-collabora" not in repr(traces)


class _NoopCollabora:
    async def convert_document_to_pdf_collabora(self, *args, **kwargs):
        raise AssertionError("PDF source must not call Collabora")


@pytest.mark.asyncio
async def test_on_demand_render_limit_cache_and_cleanup(tmp_path):
    import fitz

    doc = fitz.open()
    for number in range(MAX_PREVIEW_PAGES + 1):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {number + 1}")
    pdf = doc.tobytes()
    doc.close()
    preview_dir = tmp_path / "preview"
    preview_dir.mkdir()
    tool = PagePreviewTool(
        request(
            document_kind="pdf",
            content=pdf,
            filename="x.pdf",
            content_type="application/pdf",
        ),
        _NoopCollabora(),
        preview_dir,
    )
    first = await tool.view_page(1)
    assert first.binary_results_for_llm
    binary = first.binary_results_for_llm[0]
    assert binary.mime_type == "image/png"
    assert base64.b64decode(binary.data).startswith(b"\x89PNG\r\n\x1a\n")
    await tool.view_page(1)
    for page_number in range(2, MAX_PREVIEW_PAGES + 1):
        await tool.view_page(page_number)
    assert len(list(preview_dir.glob("*.png"))) == MAX_PREVIEW_PAGES
    with pytest.raises(ValueError, match="preview limit"):
        await tool.view_page(MAX_PREVIEW_PAGES + 1)


@pytest.mark.asyncio
async def test_fake_copilot_session_disables_github_user_fallback_and_cleans_run_dirs(
    caplog,
):
    captured = {}
    traces = []

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            _ = args

        async def run(self, prompt):
            captured["prompt"] = prompt
            provider = captured["context_providers"][0]
            captured["monty_state"] = (
                provider._execute_code_tool.build_serializable_state()
            )
            source_dir = Path(captured["client"]._options.working_directory)
            captured["source_dir"] = source_dir
            captured["state_dir"] = Path(captured["client"]._options.base_directory)
            captured["source_files"] = [
                (item.name, item.read_bytes()) for item in source_dir.iterdir()
            ]
            monty_tools = {tool.name: tool for tool in provider.get_tools()}
            captured["manifest"] = await monty_tools["document_manifest"]()
            rows = await monty_tools["read_workbook_range"](
                sheet="Sheet1", start_row=1, end_row=2, start_column=1, end_column=2
            )
            await monty_tools["submit_extraction"](
                payload={"products": [{"title": rows["rows"][1][0]["value"]}]}
            )
            return SimpleNamespace(text="")

    service = LLMService(
        collabora=_NoopCollabora(), supabase=SimpleNamespace(), agent_factory=FakeAgent
    )
    with caplog.at_level(logging.DEBUG):
        result = await service.extract(
            request(trace_event=lambda **event: traces.append(event))
        )
    assert result.products[0].title == "One"
    client = captured["client"]
    assert client._options.use_logged_in_user is False
    assert client._options.mode == "copilot-cli"
    assert client._options.env == {"COPILOT_DISABLE_KEYTAR": "1"}
    assert captured["source_files"] == [("catalog.csv", b"Title,SKU\nOne,A-1\n")]
    assert captured["monty_state"]["file_mounts"] == [
        {
            "host_path": str(captured["source_dir"].resolve()),
            "mount_path": "/input",
            "mode": "read-only",
            "write_bytes_limit": None,
        }
    ]
    assert set(captured["default_options"]["available_tools"]) == {
        "custom:document_manifest",
        "custom:execute_code",
        "custom:read_workbook_range",
        "custom:submit_extraction",
        "custom:validate_extraction",
    }
    assert {
        getattr(tool, "name", getattr(tool, "__name__", ""))
        for tool in captured["tools"]
    } == {
        "document_manifest",
        "read_workbook_range",
        "validate_extraction",
        "submit_extraction",
    }
    safety = captured["default_options"]
    assert safety["working_directory"] == str(captured["source_dir"])
    assert safety["enable_session_telemetry"] is False
    assert safety["skip_custom_instructions"] is True
    assert safety["enable_config_discovery"] is False
    assert safety["enable_on_demand_instruction_discovery"] is False
    assert safety["enable_file_hooks"] is False
    assert safety["enable_host_git_operations"] is False
    assert safety["enable_session_store"] is False
    assert safety["enable_skills"] is False
    assert safety["mcp_servers"] == {}
    assert safety["memory"] == {"enabled": False}
    permission = captured["default_options"]["on_permission_request"]
    execute = PermissionRequestCustomTool(
        tool_description="Monty", tool_name="execute_code"
    )
    assert permission(execute, {}).kind == "approve-once"
    assert {event["phase"] for event in traces} >= {
        "copilot_permission_decision",
        "copilot_tool_start",
        "copilot_tool_success",
    }
    assert {event.get("payload_preview", {}).get("tool") for event in traces} >= {
        "document_manifest",
        "read_workbook_range",
        "submit_extraction",
    }
    assert "Title,SKU" not in repr(traces)
    assert "super-secret-key" not in repr(traces)
    assert "super-secret-key" not in caplog.text
    assert not captured["source_dir"].exists()
    assert not captured["state_dir"].exists()
    assert not Path(client._options.base_directory).exists()


@pytest.mark.asyncio
async def test_extraction_traces_reported_usage_before_malformed_response_failure():
    traces = []

    class UsageDetails:
        input_token_count = 13
        output_token_count = 5
        total_token_count = 18

    class MalformedAgent:
        def __init__(self, **kwargs):
            _ = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            _ = args

        async def run(self, prompt):
            _ = prompt
            return SimpleNamespace(text="not valid json", usage_details=UsageDetails())

    service = LLMService(
        collabora=_NoopCollabora(),
        supabase=SimpleNamespace(),
        agent_factory=MalformedAgent,
    )
    with pytest.raises(RuntimeError, match="COPILOT_EXTRACTION_INVALID"):
        await service.extract(request(trace_event=lambda **event: traces.append(event)))

    usage_events = [event for event in traces if event.get("phase") == "copilot_usage"]
    assert len(usage_events) == 1
    assert usage_events[0]["metadata"]["usage"] == {
        "prompt_tokens": 13,
        "completion_tokens": 5,
        "total_tokens": 18,
    }


@pytest.mark.asyncio
async def test_extraction_without_sdk_usage_emits_no_usage_trace():
    traces = []

    class NoUsageAgent:
        def __init__(self, **kwargs):
            _ = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            _ = args

        async def run(self, prompt):
            _ = prompt
            return SimpleNamespace(text='{"products":[{"title":"One"}]}')

    service = LLMService(
        collabora=_NoopCollabora(),
        supabase=SimpleNamespace(),
        agent_factory=NoUsageAgent,
    )
    await service.extract(request(trace_event=lambda **event: traces.append(event)))
    assert not [event for event in traces if event.get("phase") == "copilot_usage"]


@pytest.mark.asyncio
async def test_product_intelligence_traces_usage_even_when_payload_is_malformed(monkeypatch):
    from ai import agent_client

    traces = []

    class FakeClient:
        def __init__(self, **kwargs):
            _ = kwargs

        async def stop(self):
            return None

    class UsageDetails:
        input_token_count = 31
        output_token_count = 9
        total_token_count = 40

    class FakeAgent:
        def __init__(self, **kwargs):
            _ = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            _ = args

        async def run(self, prompt):
            _ = prompt
            return SimpleNamespace(text="malformed", usage_details=UsageDetails())

    monkeypatch.setattr(agent_client, "CopilotClient", FakeClient)
    monkeypatch.setattr(agent_client, "GitHubCopilotAgent", FakeAgent)
    response = await agent_client.run_product_intelligence_suggestions(
        products=[{"_product_index": 0, "title": "One"}],
        model_env={
            "OLLAMA_CLOUD_URL": "https://api.openai.com/v1",
            "OLLAMA_MODEL_ID": "gpt-test",
            "OLLAMA_API_KEY": "secret",
        },
        trace_event=lambda **event: traces.append(event),
    )

    assert response.text == "malformed"
    usage_events = [event for event in traces if event.get("phase") == "copilot_usage"]
    assert len(usage_events) == 1
    assert usage_events[0]["metadata"]["usage"]["total_tokens"] == 40


@pytest.mark.asyncio
async def test_pdf_exposes_only_pdf_evidence_tools_and_native_preview():
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Demo")
    pdf = doc.tobytes()
    doc.close()
    catalog = EvidenceCatalog(
        (EvidenceEntry(1, "Demo", 1, (0, 0, 1, 1), "a1", "azure"),)
    )
    captured = {}

    class PdfAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            _ = args

        async def run(self, prompt):
            _ = prompt
            preview = next(tool for tool in captured["tools"] if isinstance(tool, Tool))
            captured["native_preview"] = await preview.handler(
                ToolInvocation(arguments={"page_number": 1})
            )
            return SimpleNamespace(
                text='{"products":[{"title":"Demo","source_refs":[{"value_path":"title","evidence_index":1}]}]}'
            )

    service = LLMService(
        collabora=_NoopCollabora(), supabase=SimpleNamespace(), agent_factory=PdfAgent
    )
    await service.extract(
        request(
            document_kind="pdf",
            content=pdf,
            filename="catalog.pdf",
            content_type="application/pdf",
            evidence_catalog=catalog,
        )
    )
    tool_names = {
        getattr(tool, "name", getattr(tool, "__name__", ""))
        for tool in captured["tools"]
    }
    assert tool_names == {
        "document_manifest",
        "read_evidence_page",
        "search_evidence",
        "validate_extraction",
        "submit_extraction",
        "view_page",
    }
    assert captured["native_preview"].binary_results_for_llm[0].mime_type == "image/png"
    assert "custom:read_workbook_range" not in set(
        captured["default_options"]["available_tools"]
    )


@pytest.mark.asyncio
async def test_service_deletes_on_demand_preview_directory_after_pdf_run():
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Demo")
    pdf = doc.tobytes()
    doc.close()
    captured = {}

    class PreviewAgent:
        def __init__(self, **kwargs):
            self.preview = next(
                tool for tool in kwargs["tools"] if isinstance(tool, Tool)
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            _ = args

        async def run(self, prompt):
            _ = prompt
            result = await self.preview.handler(
                ToolInvocation(arguments={"page_number": 1})
            )
            assert result.binary_results_for_llm
            captured["directory"] = self.preview._preview_directory
            return SimpleNamespace(
                text='{"products":[{"title":"Demo","value_origins":{"title":"ai_assisted"}}]}'
            )

    service = LLMService(
        collabora=_NoopCollabora(),
        supabase=SimpleNamespace(),
        agent_factory=PreviewAgent,
    )
    result = await service.extract(
        request(
            document_kind="pdf",
            content=pdf,
            filename="x.pdf",
            content_type="application/pdf",
        )
    )
    assert result.products[0].title == "Demo"
    assert not captured["directory"].exists()


@pytest.mark.live_copilot
@pytest.mark.asyncio
async def test_live_byok_smoke_is_opt_in():
    import os

    if os.getenv("RUN_LIVE_COPILOT_SMOKE") != "1":
        pytest.skip("Set RUN_LIVE_COPILOT_SMOKE=1 for live BYOK smoke")
    live_model = ModelConfiguration(
        provider=os.getenv("LIVE_COPILOT_PROVIDER", "openai"),
        base_url=os.environ["LIVE_COPILOT_BASE_URL"],
        model_id=os.environ["LIVE_COPILOT_MODEL_ID"],
        api_key=os.environ["LIVE_COPILOT_API_KEY"],
    )
    service = LLMService(collabora=_NoopCollabora(), supabase=SimpleNamespace())
    result = await service.extract(request(model=live_model))
    assert isinstance(result.products, list)
