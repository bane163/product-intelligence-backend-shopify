import os
from random import randint
from typing import Annotated

from agent_framework.openai import OpenAIChatClient
from dotenv import load_dotenv

load_dotenv()


def get_weather(
    location: Annotated[str, "The location to get the weather for."],
) -> str:
    """Get the weather for a given location.
    This is a simple example tool the agent can call.
    """
    conditions = ["sunny", "cloudy", "rainy", "stormy"]
    return f"The weather in {location} is {conditions[randint(0, 3)]} with a high of {randint(10, 30)}°C."


async def run_weather_agent(query: str) -> str:
    """Create a simple weather agent using OpenAIChatClient configured for Ollama and run it.

    Reads OLLAMA_ENDPOINT and OLLAMA_MODEL from the environment. Returns the agent's
    text result as a string. Raises RuntimeError on failure.
    """
    api_key = os.getenv("OLLAMA_API_KEY")
    if not api_key:
        raise RuntimeError("OLLAMA_API_KEY required")

    base_url = os.getenv("OLLAMA_CLOUD_URL", "http://localhost:11434/v1/")
    model_id = os.getenv("OLLAMA_MODEL_ID", "deepseek-r1:8b")
    print(f"Using base_url: {base_url}, model_id: {model_id}")

    client = OpenAIChatClient(api_key=api_key, base_url=base_url, model_id=model_id)

    try:
        agent = client.create_agent(
            name="WeatherAgent",
            instructions="You are a helpful weather agent.",
            tools=get_weather,
        )

        result = await agent.run(query)
        # result may be a string or an object depending on the agent implementation; coerce to str
        return str(result)
    except Exception as exc:
        # bubble up as a runtime error for the caller to convert to HTTP errors
        raise RuntimeError(f"agent run failed: {exc}") from exc
