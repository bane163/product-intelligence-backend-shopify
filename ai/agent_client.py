import base64
import json
import os
from typing import Any, Callable, Dict

from agent_framework import AgentRunResponse, ChatMessage, TextContent, DataContent
from agent_framework.openai import OpenAIChatClient

# Use the ProductsList model as a structured response format so the agent
# returns a JSON object with a 'products' array matching ProductInput.
from .models import ProductIntelligenceSuggestionsList, ProductsList
from .prompt_loader import render_prompt
from .excel_writer import create_excel_workbook

TraceFn = Callable[..., None] | None


def _trace(
    trace_event: TraceFn,
    *,
    phase: str,
    message: str,
    level: str = "info",
    payload_preview: Any = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
    transcript_role: str | None = None,
    transcript_text: str | None = None,
    transcript_meta: dict[str, Any] | None = None,
) -> None:
    if trace_event is None:
        return
    trace_event(
        phase=phase,
        message=message,
        level=level,
        payload_preview=payload_preview,
        error=error,
        metadata=metadata,
        transcript_role=transcript_role,
        transcript_text=transcript_text,
        transcript_meta=transcript_meta,
    )


def _extract_usage(response: AgentRunResponse) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _resolve_model_env(model_env: Dict[str, str] | None) -> dict[str, str]:
    resolved = dict(os.environ)
    if model_env:
        resolved.update(model_env)
    return resolved


def _collect_product_image_uris(products: list[dict[str, Any]], *, limit: int = 12) -> list[str]:
    uris: list[str] = []
    seen: set[str] = set()

    def _add_uri(raw: Any) -> None:
        if not isinstance(raw, str):
            return
        uri = raw.strip()
        if not uri or uri in seen:
            return
        if not (uri.startswith("https://") or uri.startswith("http://") or uri.startswith("data:image/")):
            return
        seen.add(uri)
        uris.append(uri)

    for product in products:
        if not isinstance(product, dict):
            continue
        featured = product.get("featured_image")
        if isinstance(featured, dict):
            _add_uri(featured.get("url") or featured.get("src"))
        images = product.get("images")
        if isinstance(images, list):
            for image in images:
                if not isinstance(image, dict):
                    continue
                _add_uri(image.get("url") or image.get("src"))
        if len(uris) >= limit:
            break

    return uris[:limit]


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
    agent_prompt: str = "Please analyze the document and the associated image(s).",
    model_env: Dict[str, str] | None = None,
    trace_event: TraceFn = None,
) -> AgentRunResponse:
    """Create an agent and run it on the provided extracted text and png (base64).

    This encapsulates creating the OpenAI/Ollama client and running the agent so
    caller code stays small and focused.
    """
    _trace(
        trace_event,
        phase="llm_prepare",
        message="Preparing agent request payload",
        payload_preview={
            "extracted_chars": len(extracted_text),
            "png_count": len(png_bytes or []),
        },
        metadata={
            "model_name": _resolve_model_env(model_env).get(
                "OLLAMA_MODEL_ID", "deepseek-r1:8b"
            ),
            "provider": "ollama/openai-compat",
        },
    )
    client = _create_chat_client(model_env)

    instructions = (
        "You will be given two inputs:\n"
        "1) A textual extraction of the document (e.g., spreadsheet or other supported document).\n"
        "2) A PNG image rendering of the document (base64).\n\n"
        "Use both sources to identify one or more products suitable for Shopify import. "
        "Return a JSON object that matches the ProductsList schema: an object with a 'products' field, "
        "which is an array of product objects. Each product should follow the ProductInput shape: "
        "title (string), optional body_html, optional vendor, optional options (list of {name, values}), "
        "optional variants (list of variant objects with option1/2/3, sku, price, inventory_quantity), "
        "optional source_refs (list of {field, document_kind, sheet, cell, cell_range, page, bbox, value}), "
        "and optional images (list of {src, alt}).\n"
        "When spreadsheet text includes [CELL_REFS] tokens, map product evidence to sheet/cell entries "
        "and include source_refs for key fields when confidence is reasonable.\n\n"
        "Only output the JSON object (no additional commentary)."
    )

    full_prompt = (
        f"User prompt: {agent_prompt}\n\n"
        "---EXTRACTED_DOCUMENT_TEXT---\n"
        f"{extracted_text}\n\n"
        "---END---\n"
        'Return only JSON that matches the ProductsList schema: {"products": [ ... ]}.'
        "Do not include any extra keys, commentary, or markdown. Output only valid JSON."
    )

    # Request the model to return a structured ProductsList JSON payload.
    agent = client.create_agent(
        name="document_inspector",
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

    _trace(
        trace_event,
        phase="llm_request",
        message="Calling LLM for product extraction",
        payload_preview={"prompt_preview": full_prompt[:700], "image_uris_count": len(data_content_list) + len(png_b64s)},
        transcript_role="user",
        transcript_text=full_prompt,
        transcript_meta={"call": "extractor"},
    )
    response = await agent.run(user_message)
    _trace(
        trace_event,
        phase="llm_response",
        message="Received LLM extraction response",
        payload_preview=(response.text or "")[:700],
        transcript_role="assistant",
        transcript_text=(response.text or ""),
        transcript_meta={"call": "extractor"},
    )
    usage = _extract_usage(response)
    if usage:
        _trace(
            trace_event,
            phase="llm_usage",
            message="Captured LLM token usage",
            metadata={
                "model_name": _resolve_model_env(model_env).get(
                    "OLLAMA_MODEL_ID", "deepseek-r1:8b"
                ),
                "provider": "ollama/openai-compat",
                "usage": usage,
            },
            payload_preview=usage,
        )
    else:
        _trace(
            trace_event,
            phase="llm_usage",
            message="LLM token usage unavailable from provider response",
            level="warning",
            metadata={
                "model_name": _resolve_model_env(model_env).get(
                    "OLLAMA_MODEL_ID", "deepseek-r1:8b"
                ),
                "provider": "ollama/openai-compat",
            },
            payload_preview={},
        )
    return response


async def run_product_intelligence_suggestions(
    products: list[dict[str, Any]],
    model_env: Dict[str, str] | None = None,
    trace_event: TraceFn = None,
) -> AgentRunResponse:
    if not products:
        raise ValueError("No products provided for product intelligence suggestions")
    _trace(
        trace_event,
        phase="llm_prepare",
        message="Preparing product intelligence suggestion request",
        payload_preview={"products_count": len(products), "image_uris_count": len(_collect_product_image_uris(products))},
        metadata={
            "model_name": _resolve_model_env(model_env).get(
                "OLLAMA_MODEL_ID", "deepseek-r1:8b"
            ),
            "provider": "ollama/openai-compat",
        },
    )
    client = _create_chat_client(model_env)
    instructions = render_prompt("product_intelligence_suggester_instructions.txt")
    product_payload = json.dumps(products, ensure_ascii=False, indent=2)
    full_prompt = render_prompt(
        "product_intelligence_suggester_user_prompt.txt",
        products_json=product_payload,
    )
    agent = client.create_agent(
        name="product_intelligence_suggester",
        instructions=instructions,
        response_format=ProductIntelligenceSuggestionsList,
    )
    image_uris = _collect_product_image_uris(products)
    contents: list[DataContent | TextContent] = [TextContent(text=full_prompt)]
    if image_uris:
        contents.extend(DataContent(uri=uri) for uri in image_uris)
    user_message = ChatMessage(role="user", contents=contents)
    _trace(
        trace_event,
        phase="llm_request",
        message="Calling LLM for product intelligence suggestions",
        payload_preview={"prompt_preview": full_prompt[:700], "image_uris_count": len(image_uris)},
        transcript_role="user",
        transcript_text=full_prompt,
        transcript_meta={"call": "product-intelligence-suggestions"},
    )
    response = await agent.run(user_message)
    _trace(
        trace_event,
        phase="llm_response",
        message="Received product intelligence suggestion response",
        payload_preview=(response.text or "")[:700],
        transcript_role="assistant",
        transcript_text=(response.text or ""),
        transcript_meta={"call": "product-intelligence-suggestions"},
    )
    usage = _extract_usage(response)
    if usage:
        _trace(
            trace_event,
            phase="llm_usage",
            message="Captured suggestion token usage",
            metadata={
                "model_name": _resolve_model_env(model_env).get(
                    "OLLAMA_MODEL_ID", "deepseek-r1:8b"
                ),
                "provider": "ollama/openai-compat",
                "usage": usage,
            },
            payload_preview=usage,
        )
    return response


async def run_excel_writer_agent(
    products_list: ProductsList,
    output_path: str,
    agent_prompt: str = "Create a spreadsheet for the provided products.",
    model_env: Dict[str, str] | None = None,
    trace_event: TraceFn = None,
    supabase_service: Any | None = None,
) -> AgentRunResponse:
    """Create a tool-enabled agent that writes the ProductsList to a spreadsheet.

    This implementation uploads the generated CSV bytes directly to Supabase storage
    instead of writing to a local filesystem path. The agent may still call the
    tool `write_products_workbook` during its run; that tool will perform the
    upload and record the generated file metadata which is attached to the
    returned AgentRunResponse as `generated_file`.
    """

    client = _create_chat_client(model_env)

    absolute_path = os.path.abspath(output_path)

    # Holder for information the tool will populate when it uploads to storage
    generated_file: dict[str, str] = {}

    def write_products_workbook() -> str:
        """Persist the captured ProductsList to Supabase Storage and return the storage key.

        The tool writes CSV bytes in-memory and uploads them using the `save_file`
        helper. It records `file_id` and `filename` into `generated_file` for the
        caller to inspect after the agent run.
        """
        # Import here to avoid import-time dependency when Supabase isn't configured
        from .excel_writer import create_excel_bytes
        import uuid

        xlsx_bytes = create_excel_bytes(products_list)
        file_id = str(uuid.uuid4())
        requested_name = os.path.basename(absolute_path) or f"{file_id}.xlsx"
        base_name, _ = os.path.splitext(requested_name)
        filename = f"{base_name}.xlsx"

        if supabase_service is None:
            raise RuntimeError(
                "Supabase service required for storage upload in write_products_workbook tool"
            )

        # Upload to Supabase storage
        supabase_service.save_file(
            file_id,
            filename,
            xlsx_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            file_origin="workflow_output",
        )

        _trace(
            trace_event,
            phase="writer_upload",
            message="Uploaded generated file to storage",
            payload_preview={
                "file_id": file_id,
                "filename": filename,
                "bytes": len(xlsx_bytes),
            },
        )

        # Record metadata for the caller
        generated_file["file_id"] = file_id
        generated_file["filename"] = filename
        generated_file["storage_path"] = file_id

        return file_id

    write_products_workbook.__name__ = "write_products_workbook"

    agent_instructions = (
        "You generate spreadsheets for Shopify product imports. "
        "Call the tool `write_products_workbook` exactly once to create the file using the provided data. "
        "After calling the tool, respond with a confirmation that includes the saved file path or identifier."
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

    _trace(
        trace_event,
        phase="writer_request",
        message="Calling writer agent",
        payload_preview={"product_count": len(products_list.products)},
        transcript_role="user",
        transcript_text=user_prompt,
        transcript_meta={"call": "writer"},
    )
    response = await agent.run(user_message)
    _trace(
        trace_event,
        phase="writer_response",
        message="Writer agent completed",
        payload_preview=(response.text or "")[:500],
        transcript_role="assistant",
        transcript_text=(response.text or ""),
        transcript_meta={"call": "writer"},
    )
    usage = _extract_usage(response)
    if usage:
        _trace(
            trace_event,
            phase="writer_usage",
            message="Captured writer token usage",
            metadata={
                "model_name": _resolve_model_env(model_env).get(
                    "OLLAMA_MODEL_ID", "deepseek-r1:8b"
                ),
                "provider": "ollama/openai-compat",
                "usage": usage,
            },
            payload_preview=usage,
        )

    # If the agent tool uploaded to storage, attach the metadata to the response
    if generated_file:
        try:
            setattr(response, "generated_file", generated_file)
        except Exception:
            # best-effort: not critical if attaching fails
            pass

        return response

    # Fallback: if the agent skipped tool invocation, persist directly when a
    # service is available so workflow execution does not depend on model behavior.
    if supabase_service is not None:
        from .excel_writer import create_excel_bytes
        import uuid

        xlsx_bytes = create_excel_bytes(products_list)
        file_id = str(uuid.uuid4())
        requested_name = os.path.basename(absolute_path) or f"{file_id}.xlsx"
        base_name, _ = os.path.splitext(requested_name)
        filename = f"{base_name}.xlsx"
        supabase_service.save_file(
            file_id,
            filename,
            xlsx_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            file_origin="workflow_output",
        )
        generated_file = {
            "file_id": file_id,
            "filename": filename,
            "storage_path": file_id,
        }
        _trace(
            trace_event,
            phase="writer_upload_fallback",
            level="warning",
            message="Writer agent skipped tool; uploaded workbook via deterministic fallback",
            payload_preview={
                "file_id": file_id,
                "filename": filename,
                "bytes": len(xlsx_bytes),
            },
        )
        try:
            setattr(response, "generated_file", generated_file)
        except Exception:
            pass
        return response

    # Fallback: if the tool did not run or didn't upload, attempt the previous
    # disk-based heuristics so older behavior remains supported.
    if not os.path.exists(absolute_path):
        csv_path = absolute_path + ".csv"
        if os.path.exists(csv_path):
            absolute_path = csv_path
        else:
            csv_candidate = None
            resp_text = getattr(response, "text", None)
            if isinstance(resp_text, str):
                import re

                m = re.search(r"(/?[^\\s'\"<>]*?\\.csv)", resp_text)
                if m:
                    candidate = m.group(1)
                    if not os.path.isabs(candidate):
                        candidate = os.path.abspath(candidate)
                    if os.path.exists(candidate):
                        absolute_path = candidate
                        csv_candidate = candidate
            if not os.path.exists(absolute_path):
                raise RuntimeError(
                    "Writer agent did not produce the expected file at the expected location: "
                    f"{absolute_path}"
                )

    return response
