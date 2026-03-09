import pytest

from infrastructure.adapters.supabase_namespaces import SupabaseDomainAccessors


class _FakeSupabaseTarget:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def _call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return name

        return _call


def test_file_namespace_alias_maps_to_service_methods():
    target = _FakeSupabaseTarget()
    domains = SupabaseDomainAccessors(target)

    result = domains.file.save("file-id", "doc.xlsx", b"bytes")

    assert result == "save_file"
    assert target.calls[0][0] == "save_file"


def test_runs_namespace_alias_maps_history_method():
    target = _FakeSupabaseTarget()
    domains = SupabaseDomainAccessors(target)

    result = domains.runs.history("run-1")

    assert result == "get_run_history"
    assert target.calls[0][0] == "get_run_history"


def test_runs_namespace_alias_maps_delete_method():
    target = _FakeSupabaseTarget()
    domains = SupabaseDomainAccessors(target)

    result = domains.runs.delete("run-1")

    assert result == "delete_run"
    assert target.calls[0][0] == "delete_run"


def test_runs_namespace_alias_maps_queue_enqueue_method():
    target = _FakeSupabaseTarget()
    domains = SupabaseDomainAccessors(target)

    result = domains.runs.enqueue("job-1", {"job_type": "document_import"})

    assert result == "enqueue_offload_job"
    assert target.calls[0][0] == "enqueue_offload_job"


def test_namespace_rejects_unsupported_method_name():
    target = _FakeSupabaseTarget()
    domains = SupabaseDomainAccessors(target)

    with pytest.raises(AttributeError):
        _ = domains.file.nonexistent_method
