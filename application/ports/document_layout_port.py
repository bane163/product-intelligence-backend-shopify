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
    provider: str = "unknown"


@dataclass(frozen=True, slots=True)
class DocumentLayout:
    page_count: int
    anchors: tuple[DocumentAnchor, ...]
    provider: str = "unknown"
    expected_pages: tuple[int, ...] = ()
    covered_pages: tuple[int, ...] = ()

    def resolve(self, anchor_id: str) -> DocumentAnchor | None:
        matches = [anchor for anchor in self.anchors if anchor.id == anchor_id]
        return matches[0] if len(matches) == 1 else None

    def to_prompt_text(self) -> str:
        """Render evidence without asking the model to invent coordinates."""
        lines: list[str] = []
        active_page: int | None = None
        for anchor in sorted(self.anchors, key=lambda item: (item.page, item.bbox[1], item.bbox[0])):
            if anchor.page != active_page:
                active_page = anchor.page
                lines.append(f"\n--- Page {active_page} ---")
            lines.append(f"[ANCHOR:{anchor.id}] {anchor.text}")
        return "\n".join(lines).strip()


class DocumentLayoutPort(Protocol):
    async def analyze_pdf(self, content: bytes, source_artifact: str) -> DocumentLayout: ...
