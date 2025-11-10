from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from ai.test import run_weather_agent
from ai.excel_workflow import run_excel_agent_workflow


router = APIRouter(prefix="/microsoft", tags=["microsoft"])


@router.get("/agent/weather", summary="Run simple weather agent")
async def microsoft_agent_weather(location: str = Query("Seattle", description="Location to query")):
    """Run the sample weather agent (non-streaming) and return the result.

    This endpoint is intended for quick local testing. It uses environment
    variables OLLAMA_ENDPOINT and OLLAMA_MODEL to configure the underlying client.
    """
    try:
        result = await run_weather_agent(location)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"result": result}


@router.post("/agent/excel", summary="Run excel -> collabora -> pdf->png -> agent")
async def microsoft_agent_excel(
    file: UploadFile = File(...),
    prompt: str = Form("Please analyze the spreadsheet and image."),
    collabora_url: str | None = Form(None),
):
    """Accept an uploaded Excel (.xlsx) file, run the conversion + rasterization workflow,
    and invoke an agent with both the extracted text and a PNG rendering.

    Returns agent text plus a base64-encoded PNG preview and the extracted text.
    """
    try:
        file_bytes = await file.read()
        result = await run_excel_agent_workflow(file_bytes, collabora_base_url=collabora_url, agent_prompt=prompt)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")

    return {
        "agent_response": result.get("agent_response"),
        "extracted_text": result.get("extracted_text"),
        "pngs_base64_preview": (result.get("pngs_base64") or [])[:1],
    }
