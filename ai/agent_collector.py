from typing import Awaitable, Callable, Dict, Any, Optional

from agent_framework import AgentRunResponse, Executor, WorkflowContext, handler
from typing_extensions import Never

from .agent_client import run_agent_on_inputs, run_agent_on_source_with_file_search


class AgentCollector(Executor):
    """Executor that accumulates partial inputs (extracted text and png_bytes)
    and calls an agent once both are present.

    This class is extracted from the LLM workflow service so it can be unit-tested and
    reused more easily. It does not assume global environment variables; the
    agent prompt and optional model_env are injected at construction time.
    """

    def __init__(
        self,
        id: str,
        agent_prompt: str,
        model_env: Optional[Dict[str, str]] = None,
        *,
        allow_without_image: bool = True,
        trace_event=None,
        use_file_search: bool = False,
        source_filename: str | None = None,
        source_content_type: str | None = None,
        document_kind: str | None = None,
        collabora_base_url: str | None = None,
        convert_document_to_pdf: Callable[..., Awaitable[bytes]] | None = None,
    ):
        super().__init__(id=id)
        # naive in-memory buffer keyed by a single run; for demo only
        self._buffer: Dict[str, Any] = {}
        self._agent_prompt = agent_prompt
        self._model_env = model_env
        # If True, run the agent when only extracted text is available
        # (useful for CSV inputs which have no image/png).
        self._allow_without_image = allow_without_image
        self._trace_event = trace_event
        self._use_file_search = use_file_search
        self._source_filename = source_filename or "document.bin"
        self._source_content_type = source_content_type or "application/octet-stream"
        self._document_kind = document_kind or "unsupported"
        self._collabora_base_url = collabora_base_url
        self._convert_document_to_pdf = convert_document_to_pdf

    @handler
    async def handle(
        self, message: Any, ctx: WorkflowContext[AgentRunResponse, Never]
    ) -> None:
        # Keep the original file bytes so file-search flows can upload source artifacts.
        if isinstance(message, (bytes, bytearray)):
            self._buffer["source_bytes"] = bytes(message)
        elif isinstance(message, dict):
            self._buffer.update(message)
        else:
            return

        # Decide whether we have enough to run the agent. We run when:
        #  - both 'extracted' and 'png_bytes' are present, OR
        #  - 'extracted' is present and allow_without_image is True
        has_extracted = "extracted" in self._buffer
        has_png = "png_bytes" in self._buffer
        has_source_bytes = "source_bytes" in self._buffer

        should_run = (
            has_source_bytes and has_extracted
            if self._use_file_search
            else has_extracted and (has_png or self._allow_without_image)
        )

        if should_run:
            if self._trace_event:
                self._trace_event(
                    phase="agent_collect",
                    message="Collected workflow inputs for LLM call",
                    payload_preview={
                        "has_png": has_png,
                        "has_extracted": has_extracted,
                        "has_source_bytes": has_source_bytes,
                        "use_file_search": self._use_file_search,
                    },
                )
            if self._use_file_search:
                source_bytes = self._buffer.pop("source_bytes")
                result = await run_agent_on_source_with_file_search(
                    source_bytes=source_bytes,
                    source_filename=self._source_filename,
                    source_content_type=self._source_content_type,
                    document_kind=self._document_kind,
                    agent_prompt=self._agent_prompt,
                    model_env=self._model_env,
                    trace_event=self._trace_event,
                    convert_document_to_pdf=self._convert_document_to_pdf,
                    collabora_base_url=self._collabora_base_url,
                )
            else:
                extracted = self._buffer.pop("extracted")
                png_bytes: list[bytes] | None = (
                    self._buffer.pop("png_bytes") if has_png else None
                )

                # Delegate to helper which encapsulates the client/agent creation
                result = await run_agent_on_inputs(
                    extracted,
                    png_bytes,
                    agent_prompt=self._agent_prompt,
                    model_env=self._model_env,
                    trace_event=self._trace_event,
                )

            # Forward the agent response to downstream executors so they can
            # parse the structured output (e.g., into a ProductsList).
            await ctx.send_message(result)

            # Reset buffer so a new run starts cleanly.
            self._buffer.clear()
