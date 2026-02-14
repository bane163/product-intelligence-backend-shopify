import os
import json
from typing import Any, Dict, Optional, Union

from agent_framework import (
    AgentRunResponse,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    executor,
)
from typing_extensions import Never
from dotenv import load_dotenv

load_dotenv()

from .excel_utils import extract_excel_contents, extract_csv_contents
from .collabora_utils import (
    convert_excel_to_pdf_collabora,
    convert_pdf_to_png_collabora,
)
from .agent_client import run_agent_on_inputs, run_excel_writer_agent
from .agent_collector import AgentCollector
from .models import ProductsList


def _extract_path_from_str(s: str) -> Optional[str]:
    """Try to extract a filesystem path from a string input.

    Heuristics (in order):
    - If the string is JSON and contains an "input" field, return that value.
    - If the entire string is a path that exists, return it.
    - If the string looks like a command + path, return the last whitespace token
      when it points to an existing path.
    Returns None when no candidate path is found.
    """
    s_strip = s.strip()

    # 1) JSON object as a string: '{"input":"..."}'
    if s_strip.startswith("{"):
        try:
            parsed = json.loads(s_strip)
            if isinstance(parsed, dict):
                input_val = parsed.get("input")
                if isinstance(input_val, str):
                    return input_val
        except json.JSONDecodeError:
            # Not valid JSON; fall through to other heuristics
            pass

    # 2) Plain path
    if os.path.exists(s_strip):
        return s_strip

    # 3) Command-like string: take the last token if it exists on disk
    tokens = s_strip.split()
    if tokens:
        candidate = tokens[-1]
        if os.path.exists(candidate):
            return candidate

    return None


def _read_file_bytes_from_path(path: str) -> bytes:
    if not isinstance(path, str):
        raise RuntimeError("Expected file path to be a string")
    if not os.path.exists(path):
        raise RuntimeError(f"File not found: {path}")
    with open(path, "rb") as fh:
        return fh.read()


def _resolve_payload(data: Any) -> bytes:
    """Normalize incoming data into raw bytes.

    Accepts:
    - dict with an "input" string key -> reads that file
    - string: tries JSON parse / path heuristics and reads file
    - bytes: returned as-is
    Raises RuntimeError when a string cannot be resolved to an existing path.
    """
    # dict input with explicit 'input' key
    if isinstance(data, dict):
        path = data.get("input")
        if not isinstance(path, str):
            raise RuntimeError("Expected 'input' field in dict to be a string path")
        return _read_file_bytes_from_path(path)

    # string input: try to parse/resolve to a path
    if isinstance(data, str):
        path = _extract_path_from_str(data)
        if path:
            return _read_file_bytes_from_path(path)
        # No path resolved — preserve original behavior and raise
        raise RuntimeError(f"String input didn't resolve to a file path: {data!r}")

    # Assume bytes-like
    return data


def get_agent_workflow(
    excel_input: Union[bytes, str],
    collabora_base_url: Optional[str] = None,
    agent_prompt: str = "Please analyze the spreadsheet and the associated image(s).",
    model_env: Optional[Dict[str, str]] = None,
    *,
    write_to_file: bool = False,
    output_path: Optional[str] = None,
    writer_agent_prompt: Optional[str] = None,
) -> Workflow:
    """Build and return a Workflow for the provided excel/csv bytes or a
    filesystem path to an excel/csv file.

    This function only constructs the workflow graph and returns it. It does
    not run the workflow. Use `run_excel_agent_workflow` to run it and obtain
    results.
    """
    # Determine file type upfront so we can build the correct workflow graph.
    # If the caller passed raw bytes, inspect the PK header. If the caller
    # passed a filesystem path, avoid opening the file here (per request) and
    # infer type from the extension. Assumption: .csv => CSV, otherwise
    # treat as Excel-like.
    if isinstance(excel_input, (bytes, bytearray)):
        excel_bytes = bytes(excel_input)
        is_excel = excel_bytes[:2] == b"PK" or excel_bytes[:4] == b"PK\x03\x04"
    else:
        # excel_input is assumed to be a filesystem path string. Don't read
        # the file contents here — file reading is done in the file_executor.
        path_lower = str(excel_input).lower()
        is_excel = not path_lower.endswith(".csv")

    is_csv = not is_excel

    # Simple start executor — forwards the incoming bytes to downstream nodes.
    @executor(id="file_executor")
    async def file_executor(
        data: bytes | str | dict, ctx: WorkflowContext[bytes]
    ) -> None:
        """Start executor: accepts either raw bytes or a filesystem path.

        If `data` is a path (str) the file is read here and the raw bytes are
        forwarded to downstream executors. This centralizes file access.
        """
        # Centralize payload resolution into a helper for readability.
        try:
            payload = _resolve_payload(data)
        except Exception as exc:
            # Surface a helpful message and re-raise so callers can see why
            print(f"file_executor error resolving payload: {exc}")
            raise

        await ctx.send_message(payload)

    # Extract executor — converts bytes -> extracted text and forwards a dict
    @executor(id="extract_executor")
    async def extract_executor(data: bytes, ctx: WorkflowContext[dict]) -> None:
        # Use CSV extractor for CSV files, otherwise Excel extractor.
        if is_csv:
            print("Extracting CSV contents")
            text = extract_csv_contents(data)
            # For CSVs we won't produce a png; include an empty png_bytes so
            # AgentCollector can run without waiting for image input.
            await ctx.send_message({"extracted": text, "png_bytes": None})
        else:
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

        await ctx.send_message({"png_bytes": pngs})

    # Handler to validate agent response into ProductsList and either yield or forward downstream
    @executor(id="handle_products_response")
    async def handle_products_response(
        response: AgentRunResponse, ctx: WorkflowContext[ProductsList, ProductsList]
    ) -> None:
        """Parse the agent response into a ProductsList and yield it."""

        if isinstance(response.value, ProductsList):
            products_list = response.value
        elif response.value is not None:
            products_list = ProductsList.model_validate(response.value)
        else:
            products_list = ProductsList.model_validate_json(response.text)

        print(f"The products list contains: {products_list}")

        if write_to_file:
            await ctx.send_message(products_list)
        else:
            await ctx.yield_output(products_list)

    excel_output_path: Optional[str] = None
    writer_prompt = (
        writer_agent_prompt
        or "Use the available tool to write the provided products to an Excel workbook and confirm the saved path."
    )

    if write_to_file:
        excel_output_path = os.path.abspath(
            output_path
            if output_path is not None
            else os.path.join(os.getcwd(), "agent_products.csv")
        )

        @executor(id="excel_writer_executor")
        async def excel_writer_executor(
            products_list: ProductsList, ctx: WorkflowContext[Never, str]
        ) -> None:
            response = await run_excel_writer_agent(
                products_list,
                output_path=excel_output_path,
                agent_prompt=writer_prompt,
                model_env=model_env,
            )
            response_text = response.text or f"Workbook saved to {excel_output_path}"
            print(f"Excel writer agent response: {response_text}")
            await ctx.yield_output(excel_output_path)

    # Agent-collector executor: accumulate partial inputs and call the agent
    # The implementation lives in `ai.agent_collector.AgentCollector` and is
    # instantiated here, passing the prompt and model environment.
    agent_collector = AgentCollector(
        id="agent_collector",
        agent_prompt=agent_prompt,
        model_env=model_env,
        allow_without_image=is_csv,
    )

    # Build the workflow graph. For CSV inputs skip the Collabora conversion
    # chain entirely and wire file -> extract -> agent_collector. For Excel
    # inputs include the conversion executors so a png preview is generated.
    builder = WorkflowBuilder().set_start_executor(file_executor)
    builder.add_edge(file_executor, extract_executor)

    if not is_csv:
        builder.add_edge(file_executor, convert_to_pdf_executor)
        builder.add_edge(convert_to_pdf_executor, pdf_to_png_executor)
        builder.add_edge(pdf_to_png_executor, agent_collector)

    builder.add_edge(extract_executor, agent_collector)

    # Route the agent_collector output into the products handler which will
    # validate JSON into ProductsList and yield the final workflow output.
    builder.add_edge(agent_collector, handle_products_response)

    if write_to_file and excel_output_path is not None:
        builder.add_edge(handle_products_response, excel_writer_executor)

    workflow = builder.build()

    # Attach the normalized starting bytes to the workflow so callers who want
    # to run it directly with the same in-memory bytes can do so. The
    # workflow.run() call should receive bytes; callers of get_agent_workflow
    # can therefore pass either the original path or the bytes they have.
    # (We still return the Workflow object as before.)
    return workflow


async def run_excel_agent_workflow(
    excel_input: Union[bytes, str],
    collabora_base_url: Optional[str] = None,
    agent_prompt: str = "Please analyze the spreadsheet and the associated image(s).",
    model_env: Optional[Dict[str, str]] = None,
    *,
    write_to_file: bool = False,
    output_path: Optional[str] = None,
    writer_agent_prompt: Optional[str] = None,
) -> ProductsList | str | None:
    """Run the workflow built for the given bytes or filesystem path and
    return the ProductsList or workbook path.

    This function builds the workflow via `get_agent_workflow`, runs it, and
    returns either the first ProductsList output, the path to a generated workbook
    (when `write_to_file=True`), or None if nothing was produced.
    """
    workflow = get_agent_workflow(
        excel_input,
        collabora_base_url=collabora_base_url,
        agent_prompt=agent_prompt,
        model_env=model_env,
        write_to_file=write_to_file,
        output_path=output_path,
        writer_agent_prompt=writer_agent_prompt,
    )

    # Pass the original input through to the workflow run. If a filesystem
    # path was provided, the `file_executor` will read it; if bytes were
    # provided, they will be forwarded directly.
    events = await workflow.run(excel_input)

    outputs = events.get_outputs() or []
    if outputs:
        return outputs[0]

    return None
