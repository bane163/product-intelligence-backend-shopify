from types import SimpleNamespace

from application.services.run_event_emitter import RunEventEmitter


class FakeRuns:
    def __init__(self):
        self.updates = []
        self.events = []

    def get_latest_run_event_seq(self, run_id):
        _ = run_id
        return 0

    def get_run(self, run_id):
        _ = run_id
        return {}

    def create_or_update_run(self, run_id, fields):
        self.updates.append((run_id, fields))

    def append_run_event(self, run_id, event, seq):
        self.events.append((run_id, event, seq))


class FakeTracing:
    def emit_run_event(self, run_id, **event):
        return {"run_id": run_id, **event}


def test_usage_events_accumulate_each_provider_response_exactly_once():
    runs = FakeRuns()
    emitter = RunEventEmitter(FakeTracing(), SimpleNamespace(runs=runs), "run-1")

    emitter.trace_event(
        phase="copilot_usage",
        message="first",
        metadata={"usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}},
    )
    emitter.trace_event(
        phase="copilot_usage",
        message="correction",
        metadata={"usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}},
    )

    token_updates = [fields for _, fields in runs.updates if "total_tokens" in fields]
    assert token_updates == [
        {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        {"prompt_tokens": 17, "completion_tokens": 7, "total_tokens": 24},
    ]


def test_non_usage_trace_leaves_run_token_fields_unwritten():
    runs = FakeRuns()
    emitter = RunEventEmitter(FakeTracing(), SimpleNamespace(runs=runs), "run-2")

    emitter.trace_event(phase="llm_response", message="no SDK usage")

    assert not [fields for _, fields in runs.updates if "total_tokens" in fields]
