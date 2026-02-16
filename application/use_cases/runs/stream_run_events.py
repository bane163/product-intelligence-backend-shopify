"""Use-case: stream run events via tracing port."""

from application.ports.tracing_port import TracingPort


def execute(tracing: TracingPort, run_id: str):
    return tracing.stream_run_events(run_id)
