-- Migration: Add extraction_progress column and pg_cron job to sync status
-- Enables pg_cron to periodically sync the latest extraction event message,
-- offload job status, and recover stale drafts at the database level.

-- 1. Enable pg_cron extension
CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA pg_catalog;
GRANT USAGE ON SCHEMA cron TO postgres;

-- 2. Add extraction_progress column to product_drafts
ALTER TABLE public.product_drafts
  ADD COLUMN IF NOT EXISTS extraction_progress TEXT;

-- 3. Create the sync function
CREATE OR REPLACE FUNCTION public.sync_draft_extraction_progress()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  -- Step 1: Sync extraction_status from offload_jobs for active drafts
  -- (offload worker updates offload_jobs but may not have updated draft yet)
  UPDATE public.product_drafts pd
  SET
    extraction_status = CASE
      WHEN oj.status IN ('running', 'claimed') THEN 'running'
      WHEN oj.status = 'succeeded' THEN 'succeeded'
      WHEN oj.status = 'failed' THEN 'failed'
      ELSE pd.extraction_status
    END,
    extraction_error = CASE
      WHEN oj.status = 'failed' THEN COALESCE(oj.error, 'Extraction failed')
      WHEN oj.status = 'succeeded' THEN NULL
      ELSE pd.extraction_error
    END,
    updated_at = timezone('utc'::text, now())
  FROM public.offload_jobs oj
  WHERE oj.draft_id IS NOT NULL
    AND oj.draft_id = pd.draft_id::text
    AND oj.job_type = 'document_import'
    AND pd.extraction_status IN ('queued', 'running')
    AND (
      (oj.status IN ('running', 'claimed') AND pd.extraction_status = 'queued')
      OR (oj.status = 'succeeded' AND pd.extraction_status != 'succeeded')
      OR (oj.status = 'failed' AND pd.extraction_status != 'failed')
    );

  -- Step 2: Sync latest event message from llm_run_events into extraction_progress
  UPDATE public.product_drafts pd
  SET
    extraction_progress = latest.message,
    updated_at = timezone('utc'::text, now())
  FROM (
    SELECT DISTINCT ON (e.run_id)
      e.run_id,
      e.message
    FROM public.llm_run_events e
    INNER JOIN public.product_drafts d
      ON d.extraction_run_id = e.run_id
    WHERE d.extraction_status IN ('queued', 'running')
      AND e.message IS NOT NULL
      AND e.message != ''
    ORDER BY e.run_id, e.seq DESC
  ) latest
  WHERE pd.extraction_run_id = latest.run_id
    AND pd.extraction_status IN ('queued', 'running')
    AND (pd.extraction_progress IS DISTINCT FROM latest.message);

  -- Step 3: Recover stale drafts stuck in queued/running for > 10 minutes
  UPDATE public.product_drafts
  SET
    extraction_status = 'failed',
    extraction_error = 'Extraction timed out (worker may not be running)',
    extraction_progress = NULL,
    updated_at = timezone('utc'::text, now())
  WHERE extraction_status IN ('queued', 'running')
    AND updated_at < timezone('utc'::text, now()) - interval '10 minutes';

  -- Step 4: Clear extraction_progress for completed drafts
  UPDATE public.product_drafts
  SET extraction_progress = NULL
  WHERE extraction_progress IS NOT NULL
    AND extraction_status NOT IN ('queued', 'running');
END;
$$;

-- 4. Schedule the cron job to run every minute
SELECT cron.schedule(
  'sync-draft-extraction-progress',
  '* * * * *',
  $$SELECT public.sync_draft_extraction_progress()$$
);
