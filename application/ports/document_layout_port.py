from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DocumentAnchor:
    id: str
    source_artifact: str
    page: int
    bbox: tuple[float, float, float, float]
    text: str
    role: str


@dataclass(frozen=True, slots=True)
class DocumentLayout:
    page_count: int
    anchors: tuple[DocumentAnchor, ...]

    def resolve(self, anchor_id: str) -> DocumentAnchor | None:
        matches = [anchor for anchor in self.anchors if anchor.id == anchor_id]
        return matches[0] if len(matches) == 1 else None


class DocumentLayoutPort(Protocol):
    async def analyze_pdf(self, content: bytes, source_artifact: str) -> DocumentLayout: ...
