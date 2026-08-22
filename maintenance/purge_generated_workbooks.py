"""Delete retired generated workbook objects without touching source evidence.

Run this command with ``--apply`` before applying the workbook-retirement
migration. Without ``--apply`` it is a read-only report.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

GENERATED_ORIGINS = frozenset(
    {"workflow_output", "draft_resume", "submitted_resume"}
)
PROTECTED_ORIGINS = frozenset(
    {"merchant_upload", "canonical_pdf", "source_highlight"}
)


@dataclass(frozen=True)
class PurgePlan:
    delete_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]


def build_purge_plan(
    metadata_rows: Iterable[dict[str, Any]], protected_references: Iterable[str]
) -> PurgePlan:
    referenced = {str(value).strip() for value in protected_references if str(value).strip()}
    delete: set[str] = set()
    protected: set[str] = set()
    for row in metadata_rows:
        path = str(row.get("storage_path") or "").strip()
        origin = str(row.get("file_origin") or "").strip().lower()
        if not path:
            continue
        if origin in PROTECTED_ORIGINS or path in referenced:
            protected.add(path)
        elif origin in GENERATED_ORIGINS:
            delete.add(path)
    return PurgePlan(tuple(sorted(delete - protected)), tuple(sorted(protected)))


def _values(rows: Iterable[dict[str, Any]], *columns: str) -> set[str]:
    return {
        str(row.get(column) or "").strip()
        for row in rows
        for column in columns
        if str(row.get(column) or "").strip()
    }


def _select(client: Any, table: str, columns: str) -> list[dict[str, Any]]:
    result = client.table(table).select(columns).execute()
    return [row for row in (result.data or []) if isinstance(row, dict)]


def collect_protected_references(client: Any) -> set[str]:
    protected = _values(
        _select(client, "product_drafts", "input_file_id"), "input_file_id"
    )
    protected |= _values(_select(client, "llm_runs", "input_file_id"), "input_file_id")
    protected |= _values(_select(client, "offload_jobs", "file_id"), "file_id")
    protected |= _values(
        _select(client, "product_source_references", "source_file_id"),
        "source_file_id",
    )
    protected |= _values(
        _select(client, "source_link_trace_events", "source_file_id,highlight_file_id"),
        "source_file_id",
        "highlight_file_id",
    )
    return protected


def apply_purge_plan(client: Any, bucket: Any, plan: PurgePlan) -> None:
    if not plan.delete_paths:
        return
    response = bucket.remove(list(plan.delete_paths))
    if isinstance(response, dict) and response.get("error"):
        raise RuntimeError(f"storage purge failed: {response['error']}")
    for path in plan.delete_paths:
        client.table("file_metadata").delete().eq("storage_path", path).execute()


def execute(*, apply: bool = False) -> PurgePlan:
    from supabase_client import get_storage, get_supabase

    client = get_supabase()
    rows = _select(client, "file_metadata", "storage_path,file_origin")
    plan = build_purge_plan(rows, collect_protected_references(client))
    print(f"generated workbook objects eligible: {len(plan.delete_paths)}")
    print(f"protected source/evidence objects: {len(plan.protected_paths)}")
    for path in plan.delete_paths:
        print(f"{'delete' if apply else 'would delete'}: {path}")
    if not apply or not plan.delete_paths:
        return plan

    bucket_name = "documents"
    bucket = get_storage().from_(bucket_name)
    apply_purge_plan(client, bucket, plan)
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="delete eligible objects")
    args = parser.parse_args()
    execute(apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
