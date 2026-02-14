from typing import Dict, Optional, Union

from ai.models import ProductsList
from .collabora_service import CollaboraService
from .supabase_service import SupabaseService


class LLMService:
    def __init__(
        self,
        *,
        collabora: CollaboraService,
        supabase: SupabaseService,
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
    ):
        from ai.excel_workflow import get_agent_workflow

        return get_agent_workflow(
            excel_input,
            collabora_base_url=collabora_base_url,
            agent_prompt=agent_prompt,
            model_env=model_env,
            write_to_file=write_to_file,
            output_path=output_path,
            writer_agent_prompt=writer_agent_prompt,
            trace_event=trace_event,
            collabora_service=self.collabora,
            supabase_service=self.supabase,
        )

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
        from ai.excel_workflow import run_excel_agent_workflow

        return await run_excel_agent_workflow(
            excel_input,
            collabora_base_url=collabora_base_url,
            agent_prompt=agent_prompt,
            model_env=model_env,
            write_to_file=write_to_file,
            output_path=output_path,
            writer_agent_prompt=writer_agent_prompt,
            trace_event=trace_event,
            collabora_service=self.collabora,
            supabase_service=self.supabase,
        )
