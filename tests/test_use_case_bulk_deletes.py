from application.use_cases.drafts.bulk_delete_product_drafts import (
    execute as bulk_delete_product_drafts_execute,
)
from application.use_cases.files.bulk_delete_files import execute as bulk_delete_files_execute
from application.use_cases.submitted.bulk_delete_submitted_documents import (
    execute as bulk_delete_submitted_documents_execute,
)


class _FileNamespace:
    def delete_file(self, file_id: str) -> bool:
        return file_id != "missing"


class _DraftNamespace:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def delete_product_draft(self, draft_id: str, *, shop_domain: str | None = None) -> bool:
        self.calls.append((draft_id, shop_domain))
        return draft_id.startswith("draft-")


class _SubmittedNamespace:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def delete_submitted_document(
        self,
        submitted_id: str,
        *,
        shop_domain: str | None = None,
    ) -> bool:
        self.calls.append((submitted_id, shop_domain))
        return submitted_id != "missing"


class _SupabaseStub:
    def __init__(self) -> None:
        self.file = _FileNamespace()
        self.drafts = _DraftNamespace()
        self.submitted = _SubmittedNamespace()


def test_bulk_delete_files_collects_deleted_and_failed_ids() -> None:
    supabase = _SupabaseStub()
    result = bulk_delete_files_execute(supabase=supabase, ids=["first", "missing", "second"])

    assert result == {"deleted_ids": ["first", "second"], "failed_ids": ["missing"]}


def test_bulk_delete_product_drafts_forwards_shop_domain() -> None:
    supabase = _SupabaseStub()
    result = bulk_delete_product_drafts_execute(
        supabase=supabase,
        ids=["draft-1", "bad-2"],
        shop_domain="tenant.myshopify.com",
    )

    assert result == {"deleted_ids": ["draft-1"], "failed_ids": ["bad-2"]}
    assert supabase.drafts.calls == [
        ("draft-1", "tenant.myshopify.com"),
        ("bad-2", "tenant.myshopify.com"),
    ]


def test_bulk_delete_submitted_documents_forwards_shop_domain() -> None:
    supabase = _SupabaseStub()
    result = bulk_delete_submitted_documents_execute(
        supabase=supabase,
        ids=["submitted-1", "missing"],
        shop_domain="tenant.myshopify.com",
    )

    assert result == {"deleted_ids": ["submitted-1"], "failed_ids": ["missing"]}
    assert supabase.submitted.calls == [
        ("submitted-1", "tenant.myshopify.com"),
        ("missing", "tenant.myshopify.com"),
    ]
