import os
from typing import Dict, Optional

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


def get_agent_workflow(
    excel_bytes: bytes,
    collabora_base_url: Optional[str] = None,
    agent_prompt: str = "Please analyze the spreadsheet and the associated image(s).",
    model_env: Optional[Dict[str, str]] = None,
    *,
    write_to_file: bool = False,
    output_path: Optional[str] = None,
    writer_agent_prompt: Optional[str] = None,
) -> Workflow:
    """Build and return a Workflow for the provided excel/csv bytes.

    This function only constructs the workflow graph and returns it. It does
    not run the workflow. Use `run_excel_agent_workflow` to run it and obtain
    results.
    """
    # Determine file type upfront: treat OOXML (xlsx) as Excel (zip PK header),
    # otherwise assume CSV. This lets us build a different workflow for CSV
    # files (no Collabora conversion / png generation).
    is_excel = excel_bytes[:2] == b"PK" or excel_bytes[:4] == b"PK\x03\x04"
    is_csv = not is_excel

    # Simple start executor — forwards the incoming bytes to downstream nodes.
    @executor(id="file_executor")
    async def file_executor(data: bytes, ctx: WorkflowContext[bytes]) -> None:
        await ctx.send_message(data)

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
            else os.path.join(os.getcwd(), "agent_products.xlsx")
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

    return workflow


async def run_excel_agent_workflow(
    excel_bytes: bytes,
    collabora_base_url: Optional[str] = None,
    agent_prompt: str = "Please analyze the spreadsheet and the associated image(s).",
    model_env: Optional[Dict[str, str]] = None,
    *,
    write_to_file: bool = False,
    output_path: Optional[str] = None,
    writer_agent_prompt: Optional[str] = None,
) -> ProductsList | str | None:
    """Run the workflow built for the given bytes and return the ProductsList or workbook path.

    This function builds the workflow via `get_agent_workflow`, runs it, and
    returns either the first ProductsList output, the path to a generated workbook
    (when `write_to_file=True`), or None if nothing was produced.
    """
    workflow = get_agent_workflow(
        excel_bytes,
        collabora_base_url=collabora_base_url,
        agent_prompt=agent_prompt,
        model_env=model_env,
        write_to_file=write_to_file,
        output_path=output_path,
        writer_agent_prompt=writer_agent_prompt,
    )

    events = await workflow.run(excel_bytes)

    outputs = events.get_outputs() or []
    if outputs:
        return outputs[0]

    return None
