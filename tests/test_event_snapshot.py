from application.use_cases.runs.get_event_snapshot import execute


class _Runs:
    def get_run_history(self, run_id, *, shop_domain=None):
        assert shop_domain == "demo.myshopify.com"
        return {
            "run": {"run_id": run_id, "status": "completed"},
            "events": [
                {"run_id": run_id, "seq": 1, "phase": "prompt", "message": "Started", "payload_preview": "secret"},
                {"run_id": run_id, "seq": 2, "phase": "request_done", "message": "Finished", "error": None},
            ],
        }


class _Supabase:
    runs = _Runs()


def test_event_snapshot_is_incremental_terminal_and_sanitized():
    snapshot = execute(
        _Supabase(),
        run_id="run-1",
        shop_domain="demo.myshopify.com",
        after_seq=1,
    )
    assert snapshot["status"] == "succeeded"
    assert snapshot["terminal"] is True
    assert snapshot["last_seq"] == 2
    assert snapshot["events"] == [
        {"run_id": "run-1", "seq": 2, "ts": "", "phase": "request_done", "level": "info", "message": "Finished"}
    ]
    assert "payload_preview" not in snapshot["events"][0]
