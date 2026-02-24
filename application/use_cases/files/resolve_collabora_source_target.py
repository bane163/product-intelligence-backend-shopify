"""Use-case: resolve a Collabora link target for non-spreadsheet source refs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from application.ports.collabora_port import CollaboraPort
from application.ports.supabase_port import SupabaseNamespacedPort
from application.services.document_formats import classify_document

_SPREADSHEET_SOURCE_KINDS = {
    "csv",
    "spreadsheet",
    "spreadsheet_legacy",
    "xlsx",
    "xlsm",
    "xls",
    "ods",
}
_GROUP_PRIORITY = {
    "Headings": 8,
    "Sections": 6,
    "Bookmarks": 6,
    "Tables": 4,
    "Frames": 2,
    "Images": 1,
}


@dataclass(frozen=True)
class _TargetCandidate:
    group: str
    label: str
    target: str


def _normalize_text(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _build_candidates(
    targets: dict[str, dict[str, str]],
) -> list[_TargetCandidate]:
    candidates: list[_TargetCandidate] = []
    for group, entries in targets.items():
        for label, target in entries.items():
            candidates.append(
                _TargetCandidate(group=group, label=label.strip(), target=target.strip())
            )
    return [
        candidate
        for candidate in candidates
        if candidate.label and candidate.target
    ]


def _score_candidate(
    *,
    query: str,
    query_tokens: set[str],
    candidate: _TargetCandidate,
) -> int:
    label_normalized = _normalize_text(candidate.label)
    target_normalized = _normalize_text(candidate.target)
    if not label_normalized and not target_normalized:
        return 0

    score = _GROUP_PRIORITY.get(candidate.group, 0)
    if query == label_normalized or query == target_normalized:
        score += 200
    if query and (query in label_normalized or query in target_normalized):
        score += 120
    if query and (
        (label_normalized and label_normalized in query)
        or (target_normalized and target_normalized in query)
    ):
        score += 80

    candidate_tokens = set((f"{label_normalized} {target_normalized}").split())
    score += len(query_tokens & candidate_tokens) * 12
    return score


def _resolve_best_target(
    *,
    source_value: str,
    targets: dict[str, dict[str, str]],
) -> _TargetCandidate | None:
    normalized_query = _normalize_text(source_value)
    if not normalized_query:
        return None

    candidates = _build_candidates(targets)
    if not candidates:
        return None

    direct_match = next(
        (
            candidate
            for candidate in candidates
            if normalized_query in {_normalize_text(candidate.label), _normalize_text(candidate.target)}
        ),
        None,
    )
    if direct_match is not None:
        return direct_match

    query_tokens = set(normalized_query.split())
    ranked_candidates = [
        (
            _score_candidate(
                query=normalized_query,
                query_tokens=query_tokens,
                candidate=candidate,
            ),
            candidate,
        )
        for candidate in candidates
    ]
    ranked_candidates.sort(
        key=lambda item: (
            item[0],
            _GROUP_PRIORITY.get(item[1].group, 0),
            len(item[1].label),
        ),
        reverse=True,
    )
    if not ranked_candidates:
        return None

    best_score, best_candidate = ranked_candidates[0]
    if best_score < 12:
        return None
    return best_candidate


def _is_spreadsheet_kind(value: str | None) -> bool:
    if not value:
        return False
    return value.strip().lower() in _SPREADSHEET_SOURCE_KINDS


async def execute(
    *,
    supabase: SupabaseNamespacedPort,
    collabora: CollaboraPort,
    source_file_id: str,
    source_value: str | None = None,
    source_document_kind: str | None = None,
    source_page: int | None = None,
    collabora_base_url: str = "http://localhost:8080",
) -> dict[str, Any]:
    file_entry = supabase.file.get_file(source_file_id)
    if not file_entry:
        raise LookupError("File not found")

    file_content = file_entry.get("content")
    if not isinstance(file_content, (bytes, bytearray)):
        raise ValueError("Stored file content is invalid")
    filename = file_entry.get("name")
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("Stored file name is invalid")
    content_type_value = file_entry.get("content_type")
    content_type = (
        content_type_value
        if isinstance(content_type_value, str) and content_type_value.strip()
        else "application/octet-stream"
    )

    document_format = classify_document(
        filename=filename,
        content_type=content_type,
        file_bytes=bytes(file_content),
    )
    if document_format.is_spreadsheet or _is_spreadsheet_kind(source_document_kind):
        return {
            "target": None,
            "matched_label": None,
            "matched_group": None,
            "target_count": 0,
            "reason": "spreadsheet",
        }

    normalized_source_value = (source_value or "").strip()
    if not normalized_source_value:
        return {
            "target": None,
            "matched_label": None,
            "matched_group": None,
            "target_count": 0,
            "reason": "missing_source_value",
        }

    targets = await collabora.extract_link_targets_collabora(
        bytes(file_content),
        filename=filename,
        content_type=content_type,
        collabora_base_url=collabora_base_url,
    )
    candidates = _build_candidates(targets)
    best_target = _resolve_best_target(source_value=normalized_source_value, targets=targets)
    if best_target is None:
        return {
            "target": None,
            "matched_label": None,
            "matched_group": None,
            "target_count": len(candidates),
            "reason": "no_match",
            "source_page": source_page,
        }

    return {
        "target": best_target.target,
        "matched_label": best_target.label,
        "matched_group": best_target.group,
        "target_count": len(candidates),
        "reason": "matched",
        "source_page": source_page,
    }
