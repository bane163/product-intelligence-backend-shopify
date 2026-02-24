-- Migration: canonical run lifecycle + tenant scope for run logging
-- Created: 2026-02-23

ALTER TABLE public.llm_runs
  ADD COLUMN IF NOT EXISTS shop_domain TEXT,
  ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS resume_token TEXT,
  ADD COLUMN IF NOT EXISTS last_completed_step TEXT,
  ADD COLUMN IF NOT EXISTS failure_code TEXT,
  ADD COLUMN IF NOT EXISTS failure_message TEXT;

UPDATE public.llm_runs
SET status = CASE lower(coalesce(status, ''))
  WHEN 'success' THEN 'succeeded'
  WHEN 'completed' THEN 'succeeded'
  WHEN 'complete' THEN 'succeeded'
  WHEN 'done' THEN 'succeeded'
  WHEN 'error' THEN 'failed'
  WHEN 'errored' THEN 'failed'
  WHEN 'pending' THEN 'queued'
  WHEN 'created' THEN 'queued'
  WHEN 'in_progress' THEN 'running'
  WHEN 'processing' THEN 'running'
  WHEN 'canceled' THEN 'cancelled'
  WHEN 'aborted' THEN 'cancelled'
  ELSE lower(coalesce(status, 'running'))
END;

UPDATE public.llm_runs
SET attempt = 1
WHERE attempt IS NULL OR attempt < 1;

CREATE INDEX IF NOT EXISTS idx_llm_runs_shop_domain_created_at
  ON public.llm_runs(shop_domain, created_at DESC);

DROP POLICY IF EXISTS "public_all_access_llm_runs" ON public.llm_runs;
DROP POLICY IF EXISTS "public_all_access_llm_run_events" ON public.llm_run_events;
DROP POLICY IF EXISTS "public_all_access_llm_run_messages" ON public.llm_run_messages;

CREATE POLICY llm_runs_tenant_access
  ON public.llm_runs
  FOR ALL
  TO public
  USING (shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', '')))
  WITH CHECK (shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', '')));

CREATE POLICY llm_run_events_tenant_access
  ON public.llm_run_events
  FOR ALL
  TO public
  USING (
    EXISTS (
      SELECT 1
      FROM public.llm_runs r
      WHERE r.run_id = llm_run_events.run_id
        AND r.shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', ''))
    )
  )
  WITH CHECK (
    EXISTS (
      SELECT 1
      FROM public.llm_runs r
      WHERE r.run_id = llm_run_events.run_id
        AND r.shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', ''))
    )
  );

CREATE POLICY llm_run_messages_tenant_access
  ON public.llm_run_messages
  FOR ALL
  TO public
  USING (
    EXISTS (
      SELECT 1
      FROM public.llm_runs r
      WHERE r.run_id = llm_run_messages.run_id
        AND r.shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', ''))
    )
  )
  WITH CHECK (
    EXISTS (
      SELECT 1
      FROM public.llm_runs r
      WHERE r.run_id = llm_run_messages.run_id
        AND r.shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', ''))
    )
  );
