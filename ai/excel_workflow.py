import os
import io
import base64
from typing import List, Optional, Dict, Any
import zipfile

import httpx
import openpyxl
from dotenv import load_dotenv

load_dotenv()

from agent_framework.openai import OpenAIChatClient


def extract_excel_contents(file_bytes: bytes, max_rows: int = 200) -> str:
    """Extracts a textual representation of the first worksheet from an .xlsx file.

    Returns a compact, newline-separated representation suitable for sending to an LLM.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    sheet = wb[wb.sheetnames[0]]

    rows = []
    for i, row in enumerate(sheet.iter_rows(values_only=True)):
        if i >= max_rows:
            rows.append("(...truncated...)")
            break
        # convert None to empty string and join with tabs for readability
        rows.append("\t".join(["" if c is None else str(c) for c in row]))

    return "\n".join(rows)


async def convert_excel_to_pdf_collabora(
    file_bytes: bytes,
    collabora_base_url: str = "http://localhost:9980",
    timeout: int = 60,
) -> bytes:
    """Sends the excel file to a Collabora (CODE/LOOL) server convert endpoint and returns PDF bytes.

    collabora_base_url should include scheme and host (and port). The function posts the file as
    multipart form data to the common convert endpoint: /lool/convert-to/pdf
    """
    convert_url = collabora_base_url.rstrip("/") + "/lool/convert-to/pdf"

    files = {"file": ("file.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(convert_url, files=files)
        resp.raise_for_status()
        return resp.content


async def convert_pdf_to_png_collabora(
    pdf_bytes: bytes,
    collabora_base_url: str = "http://localhost:9980",
    timeout: int = 60,
) -> List[bytes]:
    """Send a PDF to Collabora convert endpoint asking for PNG(s).

    Collabora may return a single PNG image (image/png) or a ZIP archive of PNGs
    (application/zip) when converting multi-page PDFs. This function detects both
    and returns a list of PNG bytes.
    """
    convert_url = collabora_base_url.rstrip("/") + "/lool/convert-to/png"

    files = {"file": ("file.pdf", pdf_bytes, "application/pdf")}

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(convert_url, files=files)
        resp.raise_for_status()
        content = resp.content
        # If it's a zip, extract png files
        try:
            b = io.BytesIO(content)
            if zipfile.is_zipfile(b):
                pngs: List[bytes] = []
                with zipfile.ZipFile(b) as z:
                    for name in z.namelist():
                        if name.lower().endswith(".png"):
                            pngs.append(z.read(name))
                return pngs
        except Exception:
            # fall through and treat as single image
            pass

        # Not a ZIP — assume the response body is a single image (PNG)
        return [content]


async def run_excel_agent_workflow(
    excel_bytes: bytes,
    collabora_base_url: Optional[str] = None,
    agent_prompt: str = "Please analyze the spreadsheet and the associated image(s).",
    model_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """End-to-end workflow: extract, convert, rasterize, and call agent with both inputs.

    Returns a dict containing the agent text, the extracted text, and base64 PNG previews.
    """
    # 1) Extract textual contents
    extracted = extract_excel_contents(excel_bytes)

    # 2) Convert using Collabora (default to localhost:8080)
    collabora_base_url = collabora_base_url or os.getenv("COLLABORA_URL", "http://localhost:8080")
    pdf_bytes = await convert_excel_to_pdf_collabora(excel_bytes, collabora_base_url=collabora_base_url)

    # 3) Convert PDF to PNG(s) via Collabora
    try:
        png_bytes_list = await convert_pdf_to_png_collabora(pdf_bytes, collabora_base_url=collabora_base_url)
    except Exception as exc:
        raise RuntimeError(f"Failed to rasterize PDF via Collabora: {exc}") from exc

    # 4) Prepare agent client (re-uses the pattern from existing ai/test.py)
    api_key = os.getenv("OLLAMA_API_KEY")
    if not api_key:
        raise RuntimeError("OLLAMA_API_KEY required to run agent")

    base_url = os.getenv("OLLAMA_CLOUD_URL", "http://localhost:11434/v1/")
    model_id = os.getenv("OLLAMA_MODEL_ID", "deepseek-r1:8b")

    client = OpenAIChatClient(api_key=api_key, base_url=base_url, model_id=model_id)

    # 5) Create agent with instructions that mention both inputs
    instructions = (
        "You will be given two inputs:\n"
        "1) A textual extraction of an Excel spreadsheet.\n"
        "2) A PNG image rendering of the spreadsheet (base64).\n"
        "Use both sources for extraction, validation, calculations, and produce a concise JSON report."
    )

    agent = client.create_agent(name="excel_inspector", instructions=instructions)

    # Prepare a single large prompt that includes the extracted text and the first PNG as base64
    # (for demonstration; a production system should use a multimodal-capable client or attachments)
    first_png_b64 = base64.b64encode(png_bytes_list[0]).decode("ascii") if png_bytes_list else ""

    full_prompt = (
        f"User prompt: {agent_prompt}\n\n"
        "---EXTRACTED_SPREADSHEET_TEXT---\n"
        f"{extracted}\n\n"
        "---PNG_BASE64_FIRST_PAGE---\n"
        f"{first_png_b64}\n"
        "---END---\n"
        "When giving results, output a JSON object with keys: summary, tables (if any), calculations (if requested)."
    )

    result = await agent.run(full_prompt)

    return {
        "agent_response": str(result),
        "extracted_text": extracted,
        "pngs_base64": [base64.b64encode(p).decode("ascii") for p in png_bytes_list],
    }
