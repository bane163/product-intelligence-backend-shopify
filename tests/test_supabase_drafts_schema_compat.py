import logging
from types import SimpleNamespace

import pytest

from services.supabase_drafts_mixin import _is_missing_column_error
from services.supabase_service import SupabaseService


class _FakeProductDraftsTable:
    def __init__(self, client: "_FakeSupabaseClient") -> None:
        self._client = client
        self._pending_payload: dict | None = None

    def upsert(self, payload, on_conflict=None):  # noqa: ANN001
        self._pending_payload = dict(payload)
        self._client.upsert_calls.append(
            {
                "table": "product_drafts",
                "payload": dict(payload),
                "on_conflict": on_conflict,
            }
        )
        return self

    def execute(self):
        payload = self._pending_payload or {}
        missing_column = self._client.missing_column
        if missing_column and missing_column in payload:
            if self._client.missing_column_error is not None:
                raise self._client.missing_column_error
            raise RuntimeError(
                f"Could not find the '{missing_column}' column of 'product_drafts' in the schema cache"
            )
        return SimpleNamespace(data=[payload])


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        missing_column: str | None = None,
        missing_column_error: Exception | None = None,
    ) -> None:
        self.missing_column = missing_column
        self.missing_column_error = missing_column_error
        self.upsert_calls: list[dict] = []

    def table(self, name: str):
        if name != "product_drafts":
            raise AssertionError(f"Unexpected table access: {name}")
        return _FakeProductDraftsTable(self)


def _build_service_with_client(client: _FakeSupabaseClient) -> SupabaseService:
    service = SupabaseService()
    service._get_supabase_client = lambda: client  # type: ignore[attr-defined]
    return service


class _StructuredApiError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__("Postgrest API error")
        self.message = message


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError('column "draft_name" does not exist'),
        _StructuredApiError(
            "Could not find the 'draft_name' column of 'product_drafts' in the schema cache"
        ),
    ],
)
def test_is_missing_column_error_supports_format_variations(exc: Exception):
    assert _is_missing_column_error(
        exc, table="product_drafts", column="draft_name"
    )
    assert not _is_missing_column_error(
        exc, table="product_drafts", column="submit_status"
    )


def test_save_product_draft_with_required_lifecycle_columns_tolerates_missing_draft_name_column(
    caplog: pytest.LogCaptureFixture,
):
    client = _FakeSupabaseClient(missing_column="draft_name")
    service = _build_service_with_client(client)

    with caplog.at_level(logging.DEBUG, logger="services.supabase_drafts_mixin"):
        result = service.save_product_draft(
            draft_id="draft-1",
            run_id="run-1",
            import_mode="auto",
            draft_name="Queued Draft",
            input_file_id="file-1",
            input_filename="queued.xlsx",
            extraction_status="running",
            extraction_run_id="run-1",
            extraction_error=None,
            submit_status=None,
            submit_run_id=None,
            submit_error=None,
            require_lifecycle_columns=True,
            products=[],
        )

    assert result["draft_name"] == "Queued Draft"
    assert len(client.upsert_calls) == 2
    first_payload = client.upsert_calls[0]["payload"]
    second_payload = client.upsert_calls[1]["payload"]
    assert "draft_name" in first_payload
    assert "draft_name" not in second_payload
    assert second_payload["extraction_status"] == "running"
    assert second_payload["extraction_run_id"] == "run-1"
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


def test_save_product_draft_with_required_lifecycle_columns_retries_sql_style_missing_draft_name_error():
    client = _FakeSupabaseClient(
        missing_column="draft_name",
        missing_column_error=RuntimeError('column "draft_name" does not exist'),
    )
    service = _build_service_with_client(client)

    result = service.save_product_draft(
        draft_id="draft-1b",
        run_id="run-1b",
        import_mode="auto",
        draft_name="Queued Draft",
        input_file_id="file-1b",
        input_filename="queued.xlsx",
        extraction_status="running",
        extraction_run_id="run-1b",
        extraction_error=None,
        submit_status=None,
        submit_run_id=None,
        submit_error=None,
        require_lifecycle_columns=True,
        products=[],
    )

    assert result["draft_name"] == "Queued Draft"
    assert len(client.upsert_calls) == 2


def test_save_product_draft_with_required_lifecycle_columns_still_fails_for_missing_lifecycle_column():
    client = _FakeSupabaseClient(missing_column="extraction_status")
    service = _build_service_with_client(client)

    with pytest.raises(RuntimeError, match="Draft lifecycle persistence failed"):
        service.save_product_draft(
            draft_id="draft-2",
            run_id="run-2",
            import_mode="auto",
            draft_name="Queued Draft",
            input_file_id="file-2",
            input_filename="queued.xlsx",
            extraction_status="running",
            extraction_run_id="run-2",
            extraction_error=None,
            submit_status=None,
            submit_run_id=None,
            submit_error=None,
            require_lifecycle_columns=True,
            products=[],
        )

    assert len(client.upsert_calls) == 1
