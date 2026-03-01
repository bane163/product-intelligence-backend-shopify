from __future__ import annotations

from types import SimpleNamespace

import pytest

import ai.agent_collector as agent_collector_module
from ai.agent_collector import AgentCollector


class _FakeWorkflowContext:
    def __init__(self) -> None:
        self.sent_messages: list[object] = []

    async def send_message(self, message: object) -> None:
        self.sent_messages.append(message)


@pytest.mark.asyncio
async def test_agent_collector_uses_file_search_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"legacy": 0, "file_search": 0}

    async def fake_run_agent_on_inputs(*args, **kwargs):
        _ = args, kwargs
        calls["legacy"] += 1
        return SimpleNamespace(value={"products": []}, text='{"products":[]}')

    async def fake_run_agent_on_source_with_file_search(*args, **kwargs):
        _ = args, kwargs
        calls["file_search"] += 1
        return SimpleNamespace(value={"products": []}, text='{"products":[]}')

    monkeypatch.setattr(
        agent_collector_module,
        "run_agent_on_inputs",
        fake_run_agent_on_inputs,
    )
    monkeypatch.setattr(
        agent_collector_module,
        "run_agent_on_source_with_file_search",
        fake_run_agent_on_source_with_file_search,
    )

    collector = AgentCollector(
        id="agent_collector",
        agent_prompt="extract products",
        allow_without_image=True,
        use_file_search=True,
        source_filename="catalog.xlsx",
        source_content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        document_kind="spreadsheet",
    )
    ctx = _FakeWorkflowContext()

    await collector.handle(b"raw-binary-input", ctx)
    assert calls["file_search"] == 0
    assert calls["legacy"] == 0

    await collector.handle({"extracted": "Sheet data"}, ctx)

    assert calls["file_search"] == 1
    assert calls["legacy"] == 0
    assert len(ctx.sent_messages) == 1
