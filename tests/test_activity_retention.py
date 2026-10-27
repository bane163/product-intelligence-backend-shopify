from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.supabase_service import SupabaseService

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)


def service():
    instance = SupabaseService()
    instance._get_supabase_client = lambda: None
    return instance


@pytest.mark.parametrize('status', ['succeeded', 'failed', 'cancelled'])
def test_exact_boundary_and_completion_based_expiry(status):
    db = service()
    row = {'run_id': 'run', 'status': status, 'created_at': (NOW - timedelta(days=90)).isoformat()}
    assert db._activity_run_expired(row, now=NOW)
    row['ended_at'] = (NOW - timedelta(days=30)).isoformat()
    assert not db._activity_run_expired(row, now=NOW)
    row['ended_at'] = (NOW - timedelta(days=30, microseconds=1)).isoformat()
    assert db._activity_run_expired(row, now=NOW)
    row['ended_at'] = NOW.isoformat()
    assert not db._activity_run_expired(row, now=NOW)


@pytest.mark.parametrize('status', ['queued', 'running'])
def test_active_runs_do_not_expire(status):
    assert not service()._activity_run_expired({'status': status, 'created_at': '2020-01-01T00:00:00Z'}, now=NOW)


@pytest.mark.parametrize('status', ['queued', 'claimed', 'running', 'retryable'])
def test_background_work_protects_terminal_run(status):
    db = service()
    db.offload_jobs['job'] = {'run_id': 'run', 'status': status}
    row = {'run_id': 'run', 'status': 'failed', 'created_at': '2020-01-01T00:00:00Z'}
    assert not db._activity_run_expired(row, now=NOW)
    db.offload_jobs['job']['status'] = 'failed'
    assert db._activity_run_expired(row, now=NOW)


def test_list_filters_expired_before_pagination_and_isolates_tenants():
    db = service()
    for run_id, shop, ended_at in [('expired', 'a', '2020-01-01T00:00:00Z'), ('live', 'a', datetime.now(timezone.utc).isoformat()), ('other', 'b', None)]:
        db.llm_runs[run_id] = {'run_id': run_id, 'status': 'succeeded', 'shop_domain': shop, 'created_at': '2026-01-01T00:00:00Z', 'ended_at': ended_at}
    assert [r['run_id'] for r in db.list_runs(limit=1, shop_domain='a')] == ['live']
    assert db.list_runs(limit=1, offset=1, shop_domain='a') == []


def test_database_listing_uses_retention_view_before_range():
    calls = []
    class Query:
        def __getattr__(self, name):
            def call(*args, **kwargs):
                calls.append((name, args, kwargs))
                return SimpleNamespace(data=[]) if name == 'execute' else self
            return call
    db = service()
    db._get_supabase_client = lambda: Query()
    db.list_runs(limit=10, offset=20, shop_domain='a')
    assert calls[0] == ('table', ('retained_activity_runs',), {})
    assert ('range', (20, 29), {}) in calls
    assert ('eq', ('shop_domain', 'a'), {}) in calls
