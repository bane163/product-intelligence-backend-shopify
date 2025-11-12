from typing import Dict, Any, Optional

from agent_framework import AgentRunResponse, Executor, WorkflowContext, handler
from typing_extensions import Never

from .agent_client import run_agent_on_inputs


class AgentCollector(Executor):
    """Executor that accumulates partial inputs (extracted text and png_bytes)
    and calls an agent once both are present.

    This class is extracted from `excel_workflow` so it can be unit-tested and
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
    ):
        super().__init__(id=id)
        # naive in-memory buffer keyed by a single run; for demo only
        self._buffer: Dict[str, Any] = {}
        self._agent_prompt = agent_prompt
        self._model_env = model_env
        # If True, run the agent when only extracted text is available
        # (useful for CSV inputs which have no image/png).
        self._allow_without_image = allow_without_image

    @handler
    async def handle(
        self, message: dict, ctx: WorkflowContext[AgentRunResponse, Never]
    ) -> None:
        # Merge incoming dict into buffer
        self._buffer.update(message)

        # Decide whether we have enough to run the agent. We run when:
        #  - both 'extracted' and 'png_bytes' are present, OR
        #  - 'extracted' is present and allow_without_image is True
        has_extracted = "extracted" in self._buffer
        has_png = "png_bytes" in self._buffer
        print(f"Has png: {has_png}, has extracted: {has_extracted}")

        if has_extracted and (has_png or self._allow_without_image):
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
            )

            # Forward the agent response to downstream executors so they can
            # parse the structured output (e.g., into a ProductsList).
            await ctx.send_message(result)

            # Reset buffer so a new run starts cleanly.
            self._buffer.clear()
