import json
from typing import Any

MAX_TEXT_LENGTH = 12000
MAX_PREVIEW_LENGTH = 1200


def redact_secrets(text: str) -> str:
    return (
        text.replace("SUPABASE_SERVICE_ROLE_KEY", "***")
        .replace("OPENAI_API_KEY", "***")
        .replace("OLLAMA_API_KEY", "***")
    )


def sanitize_text(value: Any, *, max_length: int = MAX_TEXT_LENGTH) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = redact_secrets(text)
    if len(text) > max_length:
        return f"{text[:max_length]}...(truncated)"
    return text


def sanitize_preview(value: Any) -> Any:
    return sanitize_text(value, max_length=MAX_PREVIEW_LENGTH)

