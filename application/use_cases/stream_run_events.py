"""Use-case: stream run events via tracing port."""


def execute(tracing, run_id: str):
    return tracing.stream_run_events(run_id)
