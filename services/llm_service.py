import os
import re
from typing import Any, Dict, Optional, Union

from agent_framework import (
    AgentRunResponse,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    executor,
)
from pydantic import ValidationError
from typing_extensions import Never

from ai.agent_client import run_excel_writer_agent
from ai.agent_collector import AgentCollector
from ai.document_text_utils import extract_document_contents
from ai.excel_utils import extract_csv_contents, extract_excel_contents
from ai.models import ProductsList
from application.services.document_formats import classify_document
from objects.workflow_payload import resolve_payload
from .interfaces import (
    CollaboraServiceInterface,
    LLMServiceInterface,
    SupabaseServiceInterface,
)


def _strip_markdown_json_fence(text: str) -> str:
    stripped = text.strip()
    fence_match = re.match(
        r"^```(?:json)?\s*([\s\S]*?)\s*```$", stripped, flags=re.IGNORECASE
    )
    if fence_match:
        return fence_match.group(1).strip()
    return stripped


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
        agent_prompt: str = "Please analyze the document and the associated image(s).",
        model_env: Optional[Dict[str, str]] = None,
        *,
        input_name: Optional[str] = None,
        input_content_type: Optional[str] = None,
        extraction_mode: str = "per_sheet",
        write_to_file: bool = False,
        output_path: Optional[str] = None,
        writer_agent_prompt: Optional[str] = None,
        trace_event=None,
    ) -> Workflow:
        effective_input_name = input_name
        if not effective_input_name and isinstance(excel_input, str):
            effective_input_name = str(excel_input)

        file_bytes_hint: bytes | None = None
        if isinstance(excel_input, (bytes, bytearray)):
            file_bytes_hint = bytes(excel_input)

        document_format = classify_document(
            filename=effective_input_name,
            content_type=input_content_type,
            file_bytes=file_bytes_hint,
        )
        if not document_format.is_supported:
            raise ValueError(
                "Unsupported document type for extraction workflow; provide a supported file format."
            )

        is_csv = document_format.kind == "csv"
        requires_visual_context = not is_csv
        normalized_extraction_mode = extraction_mode.strip().lower() or "per_sheet"
        max_sheets = 1 if normalized_extraction_mode == "first_sheet" else 20

        def _trace(
            phase: str,
            message: str,
            *,
            level: str = "info",
            payload_preview=None,
            error=None,
        ) -> None:
            if trace_event:
                trace_event(
                    phase=phase,
                    message=message,
                    level=level,
                    payload_preview=payload_preview,
                    error=error,
                )

        @executor(id="file_executor")
        async def file_executor(
            data: bytes | str | dict, ctx: WorkflowContext[bytes]
        ) -> None:
            try:
                payload = resolve_payload(data)
            except Exception as exc:
                _trace(
                    "file_resolve_error",
                    "Failed to resolve workflow payload",
                    level="error",
                    error=str(exc),
                )
                raise
            _trace(
                "file_resolved",
                "Workflow payload resolved",
                payload_preview={
                    "bytes": (
                        len(payload)
                        if isinstance(payload, (bytes, bytearray))
                        else None
                    )
                },
            )
            await ctx.send_message(payload)

        @executor(id="extract_executor")
        async def extract_executor(
            data: bytes, ctx: WorkflowContext[dict[str, Any]]
        ) -> None:
            _trace("extract_start", "Starting document extraction")
            if document_format.kind == "csv":
                text = extract_csv_contents(data)
            elif document_format.kind == "spreadsheet":
                text = extract_excel_contents(
                    data,
                    max_rows_per_sheet=200,
                    max_sheets=max_sheets,
                )
            elif document_format.kind == "spreadsheet_legacy":
                collabora = collabora_base_url or os.getenv(
                    "COLLABORA_URL", "http://localhost:8080"
                )
                converted = await self.collabora.convert_document_to_xlsx_collabora(
                    data,
                    filename=effective_input_name or "document.xls",
                    content_type=input_content_type or "application/octet-stream",
                    collabora_base_url=collabora,
                )
                text = extract_excel_contents(
                    converted,
                    max_rows_per_sheet=200,
                    max_sheets=max_sheets,
                )
            else:
                text = extract_document_contents(
                    data, document_kind=document_format.kind
                )
                if not text.strip():
                    text = (
                        f"No plain text content could be extracted from the {document_format.kind} "
                        "source. Use visual pages as primary context."
                    )

            _trace(
                "extract_done",
                "Document extraction completed",
                payload_preview={"chars": len(text), "kind": document_format.kind},
            )
            await ctx.send_message({"extracted": text})

        @executor(id="document_to_png_executor")
        async def document_to_png_executor(
            data: bytes, ctx: WorkflowContext[dict[str, Any]]
        ) -> None:
            collabora = collabora_base_url or os.getenv(
                "COLLABORA_URL", "http://localhost:8080"
            )
            pngs = await self.collabora.convert_document_to_png_collabora(
                data,
                filename=effective_input_name or "document.bin",
                content_type=input_content_type or "application/octet-stream",
                collabora_base_url=collabora,
            )
            _trace(
                "collabora_png_done",
                "Converted document to PNG pages",
                payload_preview={"pages": len(pngs)},
            )
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
                raw_text = response.text or ""
                try:
                    products_list = ProductsList.model_validate_json(raw_text)
                except ValidationError:
                    products_list = ProductsList.model_validate_json(
                        _strip_markdown_json_fence(raw_text)
                    )

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
            or "Use the available tool to write the provided products to a spreadsheet and confirm the saved path."
        )
        if write_to_file:
            excel_output_path = os.path.abspath(
                output_path
                if output_path is not None
                else os.path.join(os.getcwd(), "import-products.xlsx")
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
                    await ctx.yield_output(
                        {
                            **generated,
                            "products": products_list.model_dump(mode="json").get(
                                "products", []
                            ),
                        }
                    )
                else:
                    await ctx.yield_output(
                        {
                            "workbook_path": excel_output_path,
                            "products": products_list.model_dump(mode="json").get(
                                "products", []
                            ),
                        }
                    )

        agent_collector = AgentCollector(
            id="agent_collector",
            agent_prompt=agent_prompt,
            model_env=model_env,
            allow_without_image=is_csv,
            trace_event=trace_event,
        )

        builder = WorkflowBuilder().set_start_executor(file_executor)
        builder.add_edge(file_executor, extract_executor)
        if requires_visual_context:
            builder.add_edge(file_executor, document_to_png_executor)
            builder.add_edge(document_to_png_executor, agent_collector)
        builder.add_edge(extract_executor, agent_collector)
        builder.add_edge(agent_collector, handle_products_response)
        if write_to_file and excel_output_path is not None:
            builder.add_edge(handle_products_response, excel_writer_executor)
        return builder.build()

    async def run_excel_agent_workflow(
        self,
        excel_input: Union[bytes, str],
        collabora_base_url: Optional[str] = None,
        agent_prompt: str = "Please analyze the document and the associated image(s).",
        model_env: Optional[Dict[str, str]] = None,
        *,
        input_name: Optional[str] = None,
        input_content_type: Optional[str] = None,
        extraction_mode: str = "per_sheet",
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
            input_name=input_name,
            input_content_type=input_content_type,
            extraction_mode=extraction_mode,
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
