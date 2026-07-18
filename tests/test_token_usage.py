from shared.token_usage import normalize_token_usage


class UsageDetails:
    input_token_count = 21
    output_token_count = 8
    total_token_count = 29


class Response:
    usage_details = UsageDetails()


def test_agent_framework_usage_details():
    assert normalize_token_usage({"usage_details": {"input_tokens": 12, "output_tokens": 4}}) == {
        "prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16
    }
    assert normalize_token_usage(Response()) == {
        "prompt_tokens": 21, "completion_tokens": 8, "total_tokens": 29
    }


def test_openai_and_legacy_usage_aliases():
    assert normalize_token_usage({"input_tokens": 9, "output_tokens": 3, "total_tokens": 12})["total_tokens"] == 12
    assert normalize_token_usage({"prompt_token_count": 7, "completion_token_count": 2}) == {
        "prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9
    }
