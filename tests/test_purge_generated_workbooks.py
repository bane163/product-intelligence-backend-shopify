from maintenance.purge_generated_workbooks import PurgePlan, apply_purge_plan, build_purge_plan


def test_purge_plan_targets_only_unprotected_generated_artifacts():
    rows = [
        {"storage_path": "upload.xlsx", "file_origin": "merchant_upload"},
        {"storage_path": "canonical/a.pdf", "file_origin": "canonical_pdf"},
        {"storage_path": "generated.xlsx", "file_origin": "workflow_output"},
        {"storage_path": "draft.xlsx", "file_origin": "draft_resume"},
        {"storage_path": "submitted.xlsx", "file_origin": "submitted_resume"},
    ]
    plan = build_purge_plan(rows, {"draft.xlsx", "canonical/a.pdf"})
    assert plan.delete_paths == ("generated.xlsx", "submitted.xlsx")
    assert set(plan.protected_paths) == {"upload.xlsx", "canonical/a.pdf", "draft.xlsx"}


def test_purge_plan_is_idempotent_when_no_generated_rows_remain():
    plan = build_purge_plan(
        [{"storage_path": "upload.xlsx", "file_origin": "merchant_upload"}],
        {"upload.xlsx"},
    )
    assert plan.delete_paths == ()


class _Query:
    def __init__(self, deleted):
        self.deleted = deleted
        self.path = None

    def delete(self):
        return self

    def eq(self, _column, path):
        self.path = path
        return self

    def execute(self):
        self.deleted.append(self.path)


class _Client:
    def __init__(self):
        self.deleted = []

    def table(self, _name):
        return _Query(self.deleted)


class _Bucket:
    def __init__(self):
        self.removed = []

    def remove(self, paths):
        self.removed.append(tuple(paths))
        return {}


def test_apply_and_retry_remove_only_planned_paths():
    client = _Client()
    bucket = _Bucket()
    plan = PurgePlan(("draft.xlsx", "generated.xlsx"), ("source.xlsx",))

    apply_purge_plan(client, bucket, plan)
    apply_purge_plan(client, bucket, PurgePlan((), ("source.xlsx",)))

    assert bucket.removed == [("draft.xlsx", "generated.xlsx")]
    assert client.deleted == ["draft.xlsx", "generated.xlsx"]
