import base64
import os
from typing import Dict

from agent_framework import AgentRunResponse, ChatMessage, TextContent, DataContent
from agent_framework.openai import OpenAIChatClient

# Use the ProductsList model as a structured response format so the agent
# returns a JSON object with a 'products' array matching ProductInput.
from .models import ProductsList


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
    api_key = os.getenv("OLLAMA_API_KEY")
    if not api_key:
        raise RuntimeError("OLLAMA_API_KEY required to run agent")

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1/")
    model_id = os.getenv("OLLAMA_MODEL_ID", "deepseek-r1:8b")

    client = OpenAIChatClient(api_key=api_key, base_url=base_url, model_id=model_id)

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
