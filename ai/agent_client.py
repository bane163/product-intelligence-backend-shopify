import json
import os
import uuid
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict

from agent_framework import AgentRunResponse, ChatMessage, TextContent, DataContent
from agent_framework.openai import OpenAIChatClient
from openai import AsyncOpenAI

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
    usage = getattr(response, "usage_details", None)
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    return {
        "input_token_count": getattr(usage, "input_token_count", None),
        "output_token_count": getattr(usage, "output_token_count", None),
        "total_token_count": getattr(usage, "total_token_count", None),
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


def _extract_openai_response_text(response: Any) -> str:
    output = getattr(response, "output", None)
    if not isinstance(output, list):
        return ""

    chunks: list[str] = []
    for item in output:
        item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
        if item_type != "message":
            continue
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        if not isinstance(content, list):
            continue
        for part in content:
            part_type = part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
            if part_type != "output_text":
                continue
            text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                chunks.append(text)
    return "\n".join(chunks).strip()


def _enforce_openai_strict_object_schema(node: Any) -> Any:
    if isinstance(node, dict):
        normalized = {
            key: _enforce_openai_strict_object_schema(value)
            for key, value in node.items()
        }
        if normalized.get("type") == "object":
            normalized["additionalProperties"] = False
            properties = normalized.get("properties")
            if isinstance(properties, dict):
                normalized["required"] = list(properties.keys())
            else:
                normalized["required"] = []
        return normalized
    if isinstance(node, list):
        return [_enforce_openai_strict_object_schema(item) for item in node]
    return node


def _openai_strict_products_list_schema() -> dict[str, Any]:
    schema = _enforce_openai_strict_object_schema(ProductsList.model_json_schema())
    if not isinstance(schema, dict):
        raise RuntimeError("ProductsList JSON schema must be an object")
    return schema


async def run_agent_on_source_with_file_search(
    *,
    source_bytes: bytes,
    source_filename: str,
    source_content_type: str,
    document_kind: str,
    agent_prompt: str = "Please analyze the document and extract products for Shopify import.",
    model_env: Dict[str, str] | None = None,
    trace_event: TraceFn = None,
    convert_document_to_pdf: Callable[..., Awaitable[bytes]] | None = None,
    collabora_base_url: str | None = None,
) -> AgentRunResponse:
    env = _resolve_model_env(model_env)
    api_key = env.get("OLLAMA_API_KEY")
    if not api_key:
        raise RuntimeError("OLLAMA_API_KEY required to run OpenAI file search")

    model_id = env.get("OLLAMA_MODEL_ID")
    if not model_id:
        raise RuntimeError("OLLAMA_MODEL_ID required to run OpenAI file search")

    base_url = env.get("OLLAMA_CLOUD_URL")
    client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)

    upload_bytes = source_bytes
    upload_content_type = source_content_type or "application/octet-stream"
    upload_filename = os.path.basename(source_filename or "document.bin") or "document.bin"
    normalized_kind = (document_kind or "").strip().lower()
    collabora_runtime_url = collabora_base_url or os.getenv("COLLABORA_URL", "http://localhost:8080")

    if normalized_kind in {"spreadsheet", "spreadsheet_legacy"}:
        if convert_document_to_pdf is None:
            raise RuntimeError(
                "Spreadsheet file search requires convert_document_to_pdf callback"
            )
        _trace(
            trace_event,
            phase="file_search_preprocess_start",
            message="Converting spreadsheet to PDF before indexing",
            payload_preview={"filename": upload_filename, "document_kind": normalized_kind},
        )
        upload_bytes = await convert_document_to_pdf(
            source_bytes,
            filename=source_filename or "document.xlsx",
            content_type=upload_content_type,
            collabora_base_url=collabora_runtime_url,
        )
        base_name, _ = os.path.splitext(upload_filename)
        upload_filename = f"{base_name or 'document'}.pdf"
        upload_content_type = "application/pdf"
        _trace(
            trace_event,
            phase="file_search_preprocess_done",
            message="Spreadsheet converted to PDF for indexing",
            payload_preview={"filename": upload_filename, "bytes": len(upload_bytes)},
        )

    uploaded_file_id: str | None = None
    vector_store_id: str | None = None
    try:
        _trace(
            trace_event,
            phase="file_search_upload_start",
            message="Uploading source file for vector indexing",
            payload_preview={"filename": upload_filename, "bytes": len(upload_bytes)},
        )
        uploaded_file = await client.files.create(
            file=(upload_filename, upload_bytes),
            purpose="user_data",
        )
        uploaded_file_id = getattr(uploaded_file, "id", None)
        if not uploaded_file_id:
            raise RuntimeError("OpenAI file upload did not return file id")

        vector_store = await client.vector_stores.create(
            name=f"document-search-{uuid.uuid4().hex[:8]}",
            expires_after={"anchor": "last_active_at", "days": 1},
        )
        vector_store_id = getattr(vector_store, "id", None)
        if not vector_store_id:
            raise RuntimeError("OpenAI vector store create did not return id")

        _trace(
            trace_event,
            phase="file_search_index_start",
            message="Indexing uploaded file in vector store",
            payload_preview={"vector_store_id": vector_store_id, "file_id": uploaded_file_id},
        )
        index_result = await client.vector_stores.files.create_and_poll(
            vector_store_id=vector_store_id,
            file_id=uploaded_file_id,
        )
        index_error = getattr(index_result, "last_error", None)
        index_status = getattr(index_result, "status", None)
        if index_error is not None:
            error_message = getattr(index_error, "message", None) or str(index_error)
            raise RuntimeError(f"Vector store indexing failed: {error_message}")
        if index_status and index_status != "completed":
            raise RuntimeError(f"Vector store indexing incomplete with status={index_status}")

        instructions = (
            "You are a Shopify catalog extraction assistant. "
            "Use the file_search tool to inspect the uploaded document and return only valid JSON "
            'matching this schema: {"products": [ ... ]}.'
        )
        prompt = (
            f"User prompt: {agent_prompt}\n\n"
            "Use file_search on the indexed document to extract products for Shopify import. "
            'Respond with JSON only and no markdown in the exact shape {"products":[...]}.\n'
        )

        _trace(
            trace_event,
            phase="file_search_query_start",
            message="Running OpenAI response with file_search tool",
            payload_preview={"vector_store_id": vector_store_id},
        )
        response = await client.responses.create(
            model=model_id,
            instructions=instructions,
            input=prompt,
            tools=[{"type": "file_search", "vector_store_ids": [vector_store_id]}],
            tool_choice="required",
            text={
                "format": {
                    "type": "json_schema",
                    "name": "products_list",
                    "schema": _openai_strict_products_list_schema(),
                    "strict": True,
                }
            },
        )
        response_text = _extract_openai_response_text(response)
        if not response_text:
            raise RuntimeError("OpenAI file_search response contained no output text")

        try:
            parsed = ProductsList.model_validate_json(response_text)
        except Exception:
            parsed = ProductsList.model_validate_json(_strip_markdown_json_fence(response_text))

        _trace(
            trace_event,
            phase="file_search_query_done",
            message="Received structured extraction from file_search",
            payload_preview={"products": len(parsed.products)},
        )
        return SimpleNamespace(
            value=parsed,
            text=response_text,
            usage=getattr(response, "usage", None),
        )
    finally:
        if vector_store_id:
            try:
                await client.vector_stores.delete(vector_store_id=vector_store_id)
                _trace(
                    trace_event,
                    phase="file_search_cleanup_vector_store",
                    message="Deleted temporary vector store",
                    payload_preview={"vector_store_id": vector_store_id},
                )
            except Exception as exc:
                _trace(
                    trace_event,
                    phase="file_search_cleanup_vector_store_error",
                    message="Failed deleting temporary vector store",
                    level="warning",
                    error=str(exc),
                    payload_preview={"vector_store_id": vector_store_id},
                )

        if uploaded_file_id:
            try:
                await client.files.delete(file_id=uploaded_file_id)
                _trace(
                    trace_event,
                    phase="file_search_cleanup_file",
                    message="Deleted uploaded file",
                    payload_preview={"file_id": uploaded_file_id},
                )
            except Exception as exc:
                _trace(
                    trace_event,
                    phase="file_search_cleanup_file_error",
                    message="Failed deleting uploaded file",
                    level="warning",
                    error=str(exc),
                    payload_preview={"file_id": uploaded_file_id},
                )


async def run_agent_on_inputs(
    extracted_text: str,
    png_bytes: list[bytes] | None,
    agent_prompt: str = "Please analyze the document and the associated image(s).",
    model_env: Dict[str, str] | None = None,
    trace_event: TraceFn = None,
) -> AgentRunResponse:
    """Create an agent and run it on extracted text and binary PNG inputs.

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
        "2) PNG image renderings of up to 20 document pages.\n\n"
        "Use both sources to identify one or more products suitable for Shopify import. "
        "Return a JSON object that matches the ProductsList schema: an object with a 'products' field, "
        "which is an array of product objects. Each product should follow the ProductInput shape: "
        "title (string), optional body_html, optional vendor, optional options (list of {name, values}), "
        "optional variants (list of variant objects with option1/2/3, sku, price, inventory_quantity), "
        "optional source_refs (list of {field, document_kind, source_file_id, anchor_id, sheet, cell, cell_range, page, bbox, value}), "
        "and optional images (list of {src, alt}).\n"
        "When spreadsheet text includes [CELL_REFS] tokens, map product evidence to sheet/cell entries. "
        "When PDF text includes [ANCHOR:id] tokens, every PDF source reference must return that exact anchor_id; "
        "do not return a model-generated page or bounding box because the server resolves those fields. "
        "Explicitly include separate, exact source_refs with canonical field names title, vendor, sku, "
        "and price (sku and price refer to the first displayed variant) for every extracted displayed value. "
        "Copy exact CELL_REFS coordinates; never guess a cell, page, anchor ID, or bounding box.\n\n"
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

    data_content_list = (
        [DataContent(media_type="image/png", data=png) for png in png_bytes[:20]]
        if png_bytes else []
    )

    contents: list[DataContent | TextContent] = [TextContent(text=full_prompt)]

    if data_content_list:
        contents.extend(data_content_list)

    user_message = ChatMessage(
        role="user",
        contents=contents,
    )

    _trace(
        trace_event,
        phase="llm_request",
        message="Calling LLM for product extraction",
        payload_preview={"prompt_preview": full_prompt[:700], "image_uris_count": len(data_content_list)},
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
