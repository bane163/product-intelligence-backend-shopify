import logging

from shared.metrics_signals import emit_metric_signal, signal_api_error


def test_emit_metric_signal_logs_structured_payload(
    caplog: "pytest.LogCaptureFixture",
) -> None:
    caplog.set_level(logging.INFO, logger="metrics.signals")
    emit_metric_signal("offload.queue", queue_name="offload", backlog=3, status="queued")

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "metric_signal" in message
        and '"event": "offload.queue"' in message
        and '"backlog": 3' in message
        and '"status": "queued"' in message
        for message in messages
    )


def test_signal_api_error_logs_error_signal(
    caplog: "pytest.LogCaptureFixture",
) -> None:
    caplog.set_level(logging.ERROR, logger="metrics.signals")
    signal_api_error(
        route="/api/agents/submit-products",
        method="POST",
        status_code=500,
        error="submit failed",
    )

    records = [record for record in caplog.records if record.name == "metrics.signals"]
    assert records
    assert any('"event": "api.error"' in record.getMessage() for record in records)
    assert any('"status_code": 500' in record.getMessage() for record in records)
