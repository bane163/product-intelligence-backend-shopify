from typing import Dict, Any, Optional

from agent_framework import Executor, WorkflowContext, handler
from typing_extensions import Never

from .agent_client import run_agent_on_inputs


class AgentCollector(Executor):
    """Executor that accumulates partial inputs (extracted text and png_b64)
    and calls an agent once both are present.

    This class is extracted from `excel_workflow` so it can be unit-tested and
    reused more easily. It does not assume global environment variables; the
    agent prompt and optional model_env are injected at construction time.
    """

    def __init__(
        self, id: str, agent_prompt: str, model_env: Optional[Dict[str, str]] = None
    ):
        super().__init__(id=id)
        # naive in-memory buffer keyed by a single run; for demo only
        self._buffer: Dict[str, Any] = {}
        self._agent_prompt = agent_prompt
        self._model_env = model_env

    @handler
    async def handle(self, message: dict, ctx: WorkflowContext[Never, dict]) -> None:
        # Merge incoming dict into buffer
        self._buffer.update(message)

        # If we have both pieces, run the agent and yield the output
        if "extracted" in self._buffer and "png_b64" in self._buffer:
            extracted = self._buffer.pop("extracted")
            png_b64 = self._buffer.pop("png_b64")

            # Delegate to helper which encapsulates the client/agent creation
            result = await run_agent_on_inputs(
                extracted,
                png_b64,
                agent_prompt=self._agent_prompt,
                model_env=self._model_env,
            )

            # Yield the result as the workflow output; downstream consumers can read outputs
            await ctx.yield_output(
                {
                    "agent_response": str(result),
                    "extracted_text": extracted,
                    "png_b64": png_b64,
                }
            )
