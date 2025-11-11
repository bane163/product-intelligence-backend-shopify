import os
import base64
from typing import List, Optional, Dict, Any

from agent_framework import (
    Executor,
    WorkflowBuilder,
    WorkflowContext,
    executor,
    handler,
)
from typing_extensions import Never
from dotenv import load_dotenv

load_dotenv()

from .excel_utils import extract_excel_contents
from .collabora_utils import (
    convert_excel_to_pdf_collabora,
    convert_pdf_to_png_collabora,
)
from .agent_client import run_agent_on_inputs
from .agent_collector import AgentCollector


async def run_excel_agent_workflow(
    excel_bytes: bytes,
    collabora_base_url: Optional[str] = None,
    agent_prompt: str = "Please analyze the spreadsheet and the associated image(s).",
    model_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """End-to-end workflow: extract, convert, rasterize, and call agent with both inputs.

    Returns a dict containing the agent text, the extracted text, and base64 PNG previews.
    """
    # We'll build a small Workflow using agent_framework.WorkflowBuilder.
    # Design:
    #  - file_executor: receives raw excel bytes and forwards the bytes to both
    #    extract_executor and convert_executor.
    #  - extract_executor: extracts text and sends a dict {"extracted": text}
    #  - convert_executor -> pdf_executor -> png_executor: produces a base64 png
    #    and sends a dict {"png_b64": "..."}
    #  - agent_collector: collects messages from extract and png executors; when it
    #    has both, it calls the agent and yields a single workflow output (the agent result)

    # Simple start executor — forwards the incoming bytes to downstream nodes.
    @executor(id="file_executor")
    async def file_executor(data: bytes, ctx: WorkflowContext[bytes]) -> None:
        await ctx.send_message(data)

    # Extract executor — converts bytes -> extracted text and forwards a dict
    @executor(id="extract_executor")
    async def extract_executor(data: bytes, ctx: WorkflowContext[dict]) -> None:
        text = extract_excel_contents(data)
        await ctx.send_message({"extracted": text})

    # Convert executor chain: excel bytes -> pdf -> png (base64)
    @executor(id="convert_to_pdf_executor")
    async def convert_to_pdf_executor(data: bytes, ctx: WorkflowContext[bytes]) -> None:
        collabora = collabora_base_url or os.getenv(
            "COLLABORA_URL", "http://localhost:8080"
        )
        pdf = await convert_excel_to_pdf_collabora(data, collabora_base_url=collabora)
        await ctx.send_message(pdf)

    @executor(id="pdf_to_png_executor")
    async def pdf_to_png_executor(pdf_bytes: bytes, ctx: WorkflowContext[dict]) -> None:
        collabora = collabora_base_url or os.getenv(
            "COLLABORA_URL", "http://localhost:8080"
        )
        pngs = await convert_pdf_to_png_collabora(
            pdf_bytes, collabora_base_url=collabora
        )
        # Use the first page as preview and base64 encode it
        first_b64 = base64.b64encode(pngs[0]).decode("ascii") if pngs else ""
        await ctx.send_message({"png_b64": first_b64})

    # Agent-collector executor: accumulate partial inputs and call the agent
    # The implementation lives in `ai.agent_collector.AgentCollector` and is
    # instantiated here, passing the prompt and model environment.
    agent_collector = AgentCollector(
        id="agent_collector", agent_prompt=agent_prompt, model_env=model_env
    )

    # Build the workflow graph:
    # file_executor -> extract_executor -> agent_collector
    # file_executor -> convert_to_pdf_executor -> pdf_to_png_executor -> agent_collector
    workflow = (
        WorkflowBuilder()
        .set_start_executor(file_executor)
        .add_edge(file_executor, extract_executor)
        .add_edge(file_executor, convert_to_pdf_executor)
        .add_edge(extract_executor, agent_collector)
        .add_edge(convert_to_pdf_executor, pdf_to_png_executor)
        .add_edge(pdf_to_png_executor, agent_collector)
        .build()
    )

    # Run workflow and gather outputs
    events = await workflow.run(excel_bytes)

    outputs = events.get_outputs() or []
    # Return last output or empty structure
    if outputs:
        out = outputs[-1]
        return {
            "agent_response": out.get("agent_response"),
            "extracted_text": out.get("extracted_text"),
            "pngs_base64": [out.get("png_b64")],
        }

    return {"agent_response": "", "extracted_text": "", "pngs_base64": []}
