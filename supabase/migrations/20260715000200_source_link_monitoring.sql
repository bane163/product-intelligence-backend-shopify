comment on table public.source_link_trace_events is
  'Sampled, sanitized source-link diagnostics retained for operational monitoring.';

create or replace view public.source_link_stale_attempts
with (security_invoker = true) as
select
  attempt_id,
  min(created_at) as started_at,
  extract(epoch from (now() - min(created_at)))::integer as age_seconds
from public.source_link_trace_events
where attempt_id is not null
group by attempt_id
having min(created_at) < now() - interval '60 seconds'
   and count(*) filter (where stage = 'source_link_complete' and status = 'ok') = 0;

create or replace view public.source_link_failure_alert
with (security_invoker = true) as
with attempts as (
  select
    attempt_id,
    bool_or(status = 'error') as failed
  from public.source_link_trace_events
  where created_at >= now() - interval '15 minutes'
    and attempt_id is not null
  group by attempt_id
), summary as (
  select
    count(*)::integer as attempt_count,
    count(*) filter (where failed)::integer as failure_count
  from attempts
)
select
  attempt_count,
  failure_count,
  failure_count::numeric / nullif(attempt_count, 0) as failure_ratio,
  failure_count >= 3
    and failure_count::numeric / nullif(attempt_count, 0) > 0.10 as alerting
from summary;

revoke all on public.source_link_stale_attempts from anon, authenticated;
revoke all on public.source_link_failure_alert from anon, authenticated;
