from __future__ import annotations

from contextlib import contextmanager
import contextvars
from typing import Any, Iterator
from uuid import uuid4

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
_correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)


def _normalize_identifier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def generate_request_id() -> str:
    return f"req_{uuid4()}"


def resolve_request_and_correlation_ids(
    *,
    request_id: Any = None,
    correlation_id: Any = None,
) -> tuple[str, str]:
    normalized_request = _normalize_identifier(request_id) or generate_request_id()
    normalized_correlation = (
        _normalize_identifier(correlation_id) or normalized_request
    )
    return normalized_request, normalized_correlation


@contextmanager
def bind_observability_context(
    *,
    request_id: Any = None,
    correlation_id: Any = None,
) -> Iterator[tuple[str, str]]:
    resolved_request_id, resolved_correlation_id = resolve_request_and_correlation_ids(
        request_id=request_id,
        correlation_id=correlation_id,
    )
    request_token = _request_id_var.set(resolved_request_id)
    correlation_token = _correlation_id_var.set(resolved_correlation_id)
    try:
        yield resolved_request_id, resolved_correlation_id
    finally:
        _request_id_var.reset(request_token)
        _correlation_id_var.reset(correlation_token)


def get_request_id() -> str | None:
    return _request_id_var.get()


def get_correlation_id() -> str | None:
    return _correlation_id_var.get()


def current_observability_fields() -> dict[str, str]:
    request_id = get_request_id()
    correlation_id = get_correlation_id()
    fields: dict[str, str] = {}
    if request_id:
        fields["request_id"] = request_id
    if correlation_id:
        fields["correlation_id"] = correlation_id
    return fields

