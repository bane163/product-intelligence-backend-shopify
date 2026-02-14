import os
from typing import Dict, Optional, Union

from agent_framework import AgentRunResponse, Workflow, WorkflowBuilder, WorkflowContext, executor
from typing_extensions import Never

from ai.agent_client import run_excel_writer_agent
from ai.agent_collector import AgentCollector
from ai.excel_utils import extract_csv_contents, extract_excel_contents
from ai.models import ProductsList
from objects.workflow_payload import resolve_payload
from .interfaces import CollaboraServiceInterface, LLMServiceInterface, SupabaseServiceInterface


class LLMService(LLMServiceInterface):
    def __init__(
        self,
        *,
        collabora: CollaboraServiceInterface,
        supabase: SupabaseServiceInterface,
    ):
        self.collabora = collabora
        self.supabase = supabase

    def get_agent_workflow(
        self,
        excel_input: Union[bytes, str],
        collabora_base_url: Optional[str] = None,
        agent_prompt: str = "Please analyze the spreadsheet and the associated image(s).",
        model_env: Optional[Dict[str, str]] = None,
        *,
        write_to_file: bool = False,
        output_path: Optional[str] = None,
        writer_agent_prompt: Optional[str] = None,
        trace_event=None,
    ) -> Workflow:
        if isinstance(excel_input, (bytes, bytearray)):
            excel_bytes = bytes(excel_input)
            is_excel = excel_bytes[:2] == b"PK" or excel_bytes[:4] == b"PK\x03\x04"
        else:
            path_lower = str(excel_input).lower()
            is_excel = not path_lower.endswith(".csv")

        is_csv = not is_excel

        def _trace(phase: str, message: str, *, level: str = "info", payload_preview=None, error=None) -> None:
            if trace_event:
                trace_event(
                    phase=phase,
                    message=message,
                    level=level,
                    payload_preview=payload_preview,
                    error=error,
                )

        @executor(id="file_executor")
        async def file_executor(data: bytes | str | dict, ctx: WorkflowContext[bytes]) -> None:
            try:
                payload = resolve_payload(data)
            except Exception as exc:
                _trace("file_resolve_error", "Failed to resolve workflow payload", level="error", error=str(exc))
                raise
            _trace(
                "file_resolved",
                "Workflow payload resolved",
                payload_preview={"bytes": len(payload) if isinstance(payload, (bytes, bytearray)) else None},
            )
            await ctx.send_message(payload)

        @executor(id="extract_executor")
        async def extract_executor(data: bytes, ctx: WorkflowContext[dict]) -> None:
            if is_csv:
                _trace("extract_start", "Starting CSV extraction")
                text = extract_csv_contents(data)
                _trace("extract_done", "CSV extraction completed", payload_preview={"chars": len(text)})
                await ctx.send_message({"extracted": text, "png_bytes": None})
            else:
                _trace("extract_start", "Starting Excel extraction")
                text = extract_excel_contents(data)
                _trace("extract_done", "Excel extraction completed", payload_preview={"chars": len(text)})
                await ctx.send_message({"extracted": text})

        @executor(id="convert_to_pdf_executor")
        async def convert_to_pdf_executor(data: bytes, ctx: WorkflowContext[bytes]) -> None:
            collabora = collabora_base_url or os.getenv("COLLABORA_URL", "http://localhost:8080")
            pdf = await self.collabora.convert_excel_to_pdf_collabora(data, collabora_base_url=collabora)
            _trace("collabora_pdf_done", "Converted workbook to PDF", payload_preview={"bytes": len(pdf)})
            await ctx.send_message(pdf)

        @executor(id="pdf_to_png_executor")
        async def pdf_to_png_executor(pdf_bytes: bytes, ctx: WorkflowContext[dict]) -> None:
            collabora = collabora_base_url or os.getenv("COLLABORA_URL", "http://localhost:8080")
            pngs = await self.collabora.convert_pdf_to_png_collabora(
                pdf_bytes, collabora_base_url=collabora
            )
            _trace("collabora_png_done", "Converted PDF to PNG pages", payload_preview={"pages": len(pngs)})
            await ctx.send_message({"png_bytes": pngs})

        @executor(id="handle_products_response")
        async def handle_products_response(
            response: AgentRunResponse, ctx: WorkflowContext[ProductsList, ProductsList]
        ) -> None:
            if isinstance(response.value, ProductsList):
                products_list = response.value
            elif response.value is not None:
                products_list = ProductsList.model_validate(response.value)
            else:
                products_list = ProductsList.model_validate_json(response.text)

            _trace(
                "products_parsed",
                "Parsed products list from LLM response",
                payload_preview={"products": len(products_list.products)},
            )
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
                output_path if output_path is not None else os.path.join(os.getcwd(), "agent_products.xlsx")
            )

            @executor(id="excel_writer_executor")
            async def excel_writer_executor(
                products_list: ProductsList, ctx: WorkflowContext[Never, dict | str]
            ) -> None:
                response = await run_excel_writer_agent(
                    products_list,
                    output_path=excel_output_path,
                    agent_prompt=writer_prompt,
                    model_env=model_env,
                    trace_event=trace_event,
                    supabase_service=self.supabase,
                )
                generated = getattr(response, "generated_file", None)
                if generated:
                    await ctx.yield_output(generated)
                else:
                    await ctx.yield_output(excel_output_path)

        agent_collector = AgentCollector(
            id="agent_collector",
            agent_prompt=agent_prompt,
            model_env=model_env,
            allow_without_image=is_csv,
            trace_event=trace_event,
        )

        builder = WorkflowBuilder().set_start_executor(file_executor)
        builder.add_edge(file_executor, extract_executor)
        if not is_csv:
            builder.add_edge(file_executor, convert_to_pdf_executor)
            builder.add_edge(convert_to_pdf_executor, pdf_to_png_executor)
            builder.add_edge(pdf_to_png_executor, agent_collector)
        builder.add_edge(extract_executor, agent_collector)
        builder.add_edge(agent_collector, handle_products_response)
        if write_to_file and excel_output_path is not None:
            builder.add_edge(handle_products_response, excel_writer_executor)
        return builder.build()

    async def run_excel_agent_workflow(
        self,
        excel_input: Union[bytes, str],
        collabora_base_url: Optional[str] = None,
        agent_prompt: str = "Please analyze the spreadsheet and the associated image(s).",
        model_env: Optional[Dict[str, str]] = None,
        *,
        write_to_file: bool = False,
        output_path: Optional[str] = None,
        writer_agent_prompt: Optional[str] = None,
        trace_event=None,
    ) -> ProductsList | dict | str | None:
        workflow = self.get_agent_workflow(
            excel_input,
            collabora_base_url=collabora_base_url,
            agent_prompt=agent_prompt,
            model_env=model_env,
            write_to_file=write_to_file,
            output_path=output_path,
            writer_agent_prompt=writer_agent_prompt,
            trace_event=trace_event,
        )
        events = await workflow.run(excel_input)
        outputs = events.get_outputs() or []
        if outputs:
            return outputs[0]
        return None
