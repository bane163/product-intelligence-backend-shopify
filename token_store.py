import json
import os
from typing import Optional


_TOKEN_FILE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "shopify_tokens.json")
)


def _read_tokens() -> dict[str, str]:
    try:
        with open(_TOKEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


def _write_tokens(data: dict[str, str]) -> None:
    with open(_TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_token(shop: str) -> Optional[str]:
    data = _read_tokens()
    return data.get(shop)


def save_token(shop: str, token: str) -> None:
    data = _read_tokens()
    data[shop] = token
    _write_tokens(data)
