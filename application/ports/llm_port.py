from typing import Any, Protocol

from agent_framework import Workflow

from ai.models import ProductsList


class LLMPort(Protocol):
    def get_agent_workflow(
        self,
        excel_input: bytes | str,
        collabora_base_url: str | None = None,
        agent_prompt: str = "Please analyze the document and the associated image(s).",
        model_env: dict[str, str] | None = None,
        *,
        input_name: str | None = None,
        input_content_type: str | None = None,
        extraction_mode: str = "per_sheet",
        write_to_file: bool = False,
        output_path: str | None = None,
        writer_agent_prompt: str | None = None,
        trace_event=None,
    ) -> Workflow: ...

    async def run_excel_agent_workflow(
        self,
        excel_input: bytes | str,
        collabora_base_url: str | None = None,
        agent_prompt: str = "Please analyze the document and the associated image(s).",
        model_env: dict[str, str] | None = None,
        *,
        input_name: str | None = None,
        input_content_type: str | None = None,
        extraction_mode: str = "per_sheet",
        write_to_file: bool = False,
        output_path: str | None = None,
        writer_agent_prompt: str | None = None,
        trace_event=None,
    ) -> ProductsList | dict[str, Any] | str | None: ...
