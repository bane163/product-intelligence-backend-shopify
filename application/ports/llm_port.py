from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol

from ai.models import ProductsList
from application.services.document_formats import DocumentKind
from application.services.pdf_evidence_index import EvidenceCatalog


TraceCallback = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ModelConfiguration:
    provider: str
    base_url: str
    model_id: str
    api_key: str
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    extra: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    document_kind: DocumentKind
    content: bytes
    filename: str
    content_type: str
    model: ModelConfiguration
    evidence_catalog: EvidenceCatalog | None = None
    validation_feedback: str | None = None
    extraction_mode: Literal["per_sheet", "first_sheet"] = "per_sheet"
    collabora_base_url: str | None = None
    trace_event: TraceCallback | None = None


class LLMPort(Protocol):
    async def extract(self, request: ExtractionRequest) -> ProductsList: ...
