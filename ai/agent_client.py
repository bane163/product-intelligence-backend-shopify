import base64
import os
from typing import Dict

from agent_framework import AgentRunResponse, ChatMessage, TextContent, DataContent
from agent_framework.openai import OpenAIChatClient

# Use the ProductsList model as a structured response format so the agent
# returns a JSON object with a 'products' array matching ProductInput.
from .models import ProductsList
from .excel_writer import create_excel_workbook


def _resolve_model_env(model_env: Dict[str, str] | None) -> dict[str, str]:
    resolved = dict(os.environ)
    if model_env:
        resolved.update(model_env)
    return resolved


def _create_chat_client(model_env: Dict[str, str] | None) -> OpenAIChatClient:
    env = _resolve_model_env(model_env)

    api_key = env.get("OLLAMA_API_KEY")
    if not api_key:
        raise RuntimeError("OLLAMA_API_KEY required to run agent")

    base_url = env.get("OLLAMA_CLOUD_URL", "http://localhost:11434/v1/")
    model_id = env.get("OLLAMA_MODEL_ID", "deepseek-r1:8b")

    return OpenAIChatClient(api_key=api_key, base_url=base_url, model_id=model_id)


async def run_agent_on_inputs(
    extracted_text: str,
    png_bytes: list[bytes] | None,
    agent_prompt: str = "Please analyze the spreadsheet and the associated image(s).",
    model_env: Dict[str, str] | None = None,
) -> AgentRunResponse:
    """Create an agent and run it on the provided extracted text and png (base64).

    This encapsulates creating the OpenAI/Ollama client and running the agent so
    caller code stays small and focused.
    """
    client = _create_chat_client(model_env)

    instructions = (
        "You will be given two inputs:\n"
        "1) A textual extraction of an Excel spreadsheet.\n"
        "2) A PNG image rendering of the spreadsheet (base64).\n\n"
        "Use both sources to identify one or more products suitable for Shopify import. "
        "Return a JSON object that matches the ProductsList schema: an object with a 'products' field, "
        "which is an array of product objects. Each product should follow the ProductInput shape: "
        "title (string), optional body_html, optional vendor, optional options (list of {name, values}), "
        "optional variants (list of variant objects with option1/2/3, sku, price, inventory_quantity), "
        "and optional images (list of {src, alt}).\n\n"
        "Only output the JSON object (no additional commentary)."
    )

    full_prompt = (
        f"User prompt: {agent_prompt}\n\n"
        "---EXTRACTED_SPREADSHEET_TEXT---\n"
        f"{extracted_text}\n\n"
        "---END---\n"
        'Return only JSON that matches the ProductsList schema: {"products": [ ... ]}.'
        "Do not include any extra keys, commentary, or markdown. Output only valid JSON."
    )

    # Request the model to return a structured ProductsList JSON payload.
    agent = client.create_agent(
        name="excel_inspector",
        instructions=instructions,
        response_format=ProductsList,
    )

    data_content_list: list[DataContent] = []

    png_b64s: list[TextContent] = (
        [
            TextContent(
                text=f"---EXTRACTED_PNG_BASE64---\n{base64.b64encode(png).decode('ascii')}\n---END---\n"
            )
            for png in png_bytes
        ]
        if png_bytes
        else []
    )

    # data_content_list: list[DataContent] = (
    #     [DataContent(media_type="image/png", data=png) for png in png_bytes]
    #     if png_bytes
    #     else []
    # )

    # data_content_list: list[DataContent] = (
    #     [
    #         DataContent(
    #             uri=f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}"
    #         )
    #         for png in png_bytes
    #     ]
    #     if png_bytes
    #     else []
    # )

    contents: list[DataContent | TextContent] = [TextContent(text=full_prompt)]

    if data_content_list:
        contents.extend(data_content_list)
    if png_b64s:
        contents.extend(png_b64s)

    user_message = ChatMessage(
        role="user",
        contents=contents,
    )

    # agent.run returns an AgentRunResponse; when structured parsing succeeds,
    # response.value contains the ProductsList instance we requested.
    return await agent.run(user_message)


async def run_excel_writer_agent(
    products_list: ProductsList,
    output_path: str,
    agent_prompt: str = "Create an Excel workbook for the provided products.",
    model_env: Dict[str, str] | None = None,
) -> AgentRunResponse:
    """Create a tool-enabled agent that writes the ProductsList to an Excel workbook."""

    client = _create_chat_client(model_env)

    absolute_path = os.path.abspath(output_path)

    def write_products_workbook() -> str:
        """Persist the captured ProductsList to an Excel workbook."""

        return create_excel_workbook(products_list, absolute_path)

    write_products_workbook.__name__ = "write_products_workbook"

    agent_instructions = (
        "You generate Excel workbooks for Shopify product imports. "
        "Call the tool `write_products_workbook` exactly once to create the workbook using the provided data. "
        "After calling the tool, respond with a confirmation that includes the saved file path."
    )

    agent = client.create_agent(
        name="excel_writer_agent",
        instructions=agent_instructions,
        tools=[write_products_workbook],
    )

    products_json = products_list.model_dump_json(indent=2)
    user_prompt = (
        f"{agent_prompt}\n\n"
        f"Write the workbook to the following absolute path: {absolute_path}\n\n"
        "Products to include (JSON schema matches ProductsList):\n"
        f"{products_json}"
    )

    user_message = ChatMessage(role="user", contents=[TextContent(text=user_prompt)])

    response = await agent.run(user_message)

    if not os.path.exists(absolute_path):
        # Try an alternate CSV path next to the requested file
        csv_path = absolute_path + ".csv"
        if os.path.exists(csv_path):
            absolute_path = csv_path
        else:
            # Attempt to find a .csv path in the agent response text before failing
            csv_candidate = None
            resp_text = getattr(response, "text", None)
            if isinstance(resp_text, str):
                import re
                m = re.search(r"(/?[^\\s'\"<>]*?\\.csv)", resp_text)
                if m:
                    candidate = m.group(1)
                    # Make absolute if necessary
                    if not os.path.isabs(candidate):
                        candidate = os.path.abspath(candidate)
                    if os.path.exists(candidate):
                        absolute_path = candidate
                        csv_candidate = candidate
            if not os.path.exists(absolute_path):
                raise RuntimeError(
                    "Excel writer agent did not produce a workbook at the expected location: "
                    f"{absolute_path}"
                )

    return response
