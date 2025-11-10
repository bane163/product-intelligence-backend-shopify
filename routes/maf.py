from fastapi import APIRouter, HTTPException, Query
from ai.test import run_weather_agent


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
