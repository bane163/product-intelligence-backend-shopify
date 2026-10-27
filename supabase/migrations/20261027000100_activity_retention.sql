-- Activity logs expire; business records retain their original run references.
DO $$
DECLARE dependency record;
BEGIN
  FOR dependency IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'public.billing_usage_events'::regclass
      AND confrelid = 'public.llm_runs'::regclass AND contype = 'f'
  LOOP
    EXECUTE format('ALTER TABLE public.billing_usage_events DROP CONSTRAINT %I', dependency.conname);
  END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS llm_runs_retention_idx
  ON public.llm_runs ((coalesce(ended_at, created_at)))
  WHERE status IN ('succeeded', 'failed', 'cancelled');

CREATE INDEX IF NOT EXISTS product_suggestions_product_history_idx
  ON public.product_intelligence_suggestions (shop_domain, product_id, created_at, suggestion_id);

-- One eligibility predicate for both the list and the cleanup, before pagination.
CREATE OR REPLACE VIEW public.expired_activity_runs WITH (security_invoker = true) AS
SELECT r.run_id FROM public.llm_runs r
WHERE r.status IN ('succeeded', 'failed', 'cancelled')
  AND coalesce(r.ended_at, r.created_at) < now() - interval '30 days'
  AND NOT EXISTS (
    SELECT 1 FROM public.offload_jobs j WHERE j.run_id = r.run_id
      AND j.status NOT IN ('succeeded', 'failed', 'cancelled')
  );
CREATE OR REPLACE VIEW public.retained_activity_runs WITH (security_invoker = true) AS
SELECT r.* FROM public.llm_runs r
WHERE NOT EXISTS (SELECT 1 FROM public.expired_activity_runs e WHERE e.run_id = r.run_id);
REVOKE ALL ON public.expired_activity_runs, public.retained_activity_runs FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.expired_activity_runs, public.retained_activity_runs TO service_role;

CREATE OR REPLACE FUNCTION public.cleanup_expired_activity()
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE expired_ids text[]; deleted_count integer;
BEGIN
  -- Prevent run/job activation between eligibility and deletion.
  LOCK TABLE public.llm_runs, public.offload_jobs IN SHARE ROW EXCLUSIVE MODE;
  SELECT array_agg(r.run_id) INTO expired_ids FROM (
    SELECT r.run_id FROM public.llm_runs r
    JOIN public.expired_activity_runs e ON e.run_id = r.run_id
    FOR UPDATE OF r
  ) r;
  DELETE FROM public.offload_jobs WHERE run_id = ANY(expired_ids)
    AND status IN ('succeeded', 'failed', 'cancelled');
  DELETE FROM public.llm_runs WHERE run_id = ANY(expired_ids);
  GET DIAGNOSTICS deleted_count = ROW_COUNT;
  -- llm_run_events/messages cascade. Documents, audits, suggestions and billing do not.
  RETURN deleted_count;
END $$;
REVOKE ALL ON FUNCTION public.cleanup_expired_activity() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.cleanup_expired_activity() TO service_role;
CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA pg_catalog;
SELECT cron.schedule('cleanup-expired-activity', '0 * * * *', 'SELECT public.cleanup_expired_activity()');
-- Preservation and permission changes above must precede the initial cleanup.
SELECT public.cleanup_expired_activity();
