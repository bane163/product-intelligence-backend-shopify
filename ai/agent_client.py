import os
from typing import Dict, Any

from agent_framework.openai import OpenAIChatClient


async def run_agent_on_inputs(
    extracted_text: str,
    png_b64: str,
    agent_prompt: str = "Please analyze the spreadsheet and the associated image(s).",
    model_env: Dict[str, str] | None = None,
) -> Any:
    """Create an agent and run it on the provided extracted text and png (base64).

    This encapsulates creating the OpenAI/Ollama client and running the agent so
    caller code stays small and focused.
    """
    api_key = os.getenv("OLLAMA_API_KEY")
    if not api_key:
        raise RuntimeError("OLLAMA_API_KEY required to run agent")

    base_url = os.getenv("OLLAMA_CLOUD_URL", "http://localhost:11434/v1/")
    model_id = os.getenv("OLLAMA_MODEL_ID", "deepseek-r1:8b")

    client = OpenAIChatClient(api_key=api_key, base_url=base_url, model_id=model_id)

    instructions = (
        "You will be given two inputs:\n"
        "1) A textual extraction of an Excel spreadsheet.\n"
        "2) A PNG image rendering of the spreadsheet (base64).\n"
        "Use both sources for extraction, validation, calculations, and produce a concise JSON report."
    )

    agent = client.create_agent(name="excel_inspector", instructions=instructions)

    full_prompt = (
        f"User prompt: {agent_prompt}\n\n"
        "---EXTRACTED_SPREADSHEET_TEXT---\n"
        f"{extracted_text}\n\n"
        "---PNG_BASE64_FIRST_PAGE---\n"
        f"{png_b64}\n"
        "---END---\n"
        "When giving results, output a JSON object with keys: summary, tables (if any), calculations (if requested)."
    )

    return await agent.run(full_prompt)
