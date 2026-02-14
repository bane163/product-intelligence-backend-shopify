import json
import os
from typing import Any


def extract_path_from_str(value: str) -> str | None:
    text = value.strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                input_val = parsed.get("input")
                if isinstance(input_val, str):
                    return input_val
        except json.JSONDecodeError:
            pass

    if os.path.exists(text):
        return text

    tokens = text.split()
    if tokens and os.path.exists(tokens[-1]):
        return tokens[-1]
    return None


def read_file_bytes_from_path(path: str) -> bytes:
    if not isinstance(path, str):
        raise RuntimeError("Expected file path to be a string")
    if not os.path.exists(path):
        raise RuntimeError(f"File not found: {path}")
    with open(path, "rb") as fh:
        return fh.read()


def resolve_payload(data: Any) -> bytes:
    if isinstance(data, dict):
        path = data.get("input")
        if not isinstance(path, str):
            raise RuntimeError("Expected 'input' field in dict to be a string path")
        return read_file_bytes_from_path(path)

    if isinstance(data, str):
        path = extract_path_from_str(data)
        if path:
            return read_file_bytes_from_path(path)
        raise RuntimeError(f"String input didn't resolve to a file path: {data!r}")

    return data

