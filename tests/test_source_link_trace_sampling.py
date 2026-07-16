import uuid

import pytest

from services import source_link_trace


def test_sampling_is_stable_for_an_entire_attempt(monkeypatch):
    monkeypatch.setenv("SOURCE_LINK_TRACE_SAMPLE_RATE", "0.5")
    attempt = str(uuid.uuid4())
    assert len({source_link_trace.sampled(attempt) for _ in range(20)}) == 1


@pytest.mark.parametrize("value", ["-0.1", "1.1", "not-a-number"])
def test_invalid_sample_rate_is_rejected(monkeypatch, value):
    monkeypatch.setenv("SOURCE_LINK_TRACE_SAMPLE_RATE", value)
    with pytest.raises(ValueError, match="0 to 1"):
        source_link_trace.sample_rate()


def test_production_defaults_to_five_percent(monkeypatch):
    monkeypatch.delenv("SOURCE_LINK_TRACE_SAMPLE_RATE", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert source_link_trace.sample_rate() == 0.05


def test_tracing_defaults_on_in_staging_and_production(monkeypatch):
    monkeypatch.delenv("SOURCE_LINK_TRACE_ENABLED", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert source_link_trace.enabled() is True
