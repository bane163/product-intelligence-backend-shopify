from types import SimpleNamespace

from api.agents.drafts import _enrich_token_usage


def test_drafts_receive_accumulated_related_run_token_totals():
    class Runs:
        def get_run_summaries(self, run_ids, *, shop_domain):
            assert run_ids == ["extract-run", "submit-run", "missing-run"]
            assert shop_domain == "shop.myshopify.com"
            return {
                "extract-run": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
                "submit-run": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
                "missing-run": {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
            }

    drafts = [{
        "draft_id": "draft-1",
        "run_id": "extract-run",
        "extraction_run_id": "extract-run",
        "submit_run_id": "submit-run",
    }, {
        "draft_id": "draft-2",
        "run_id": "missing-run",
    }]
    ctx = SimpleNamespace(supabase=SimpleNamespace(runs=Runs()))

    _enrich_token_usage(drafts, ctx, "shop.myshopify.com")

    assert drafts[0]["token_usage"] == {
        "prompt_tokens": 25,
        "completion_tokens": 10,
        "total_tokens": 35,
        "recorded_run_count": 2,
        "related_run_count": 2,
    }
    assert drafts[1]["token_usage"] is None
