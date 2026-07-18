-- Remove phantom regeneration activity created by product searches before the
-- regeneration lifecycle was moved into its proper endpoint. Preserve any run
-- that recorded processing or a terminal event.
DELETE FROM public.llm_runs AS run
WHERE run.source = 'product_intelligence_regeneration'
  AND run.status IN ('queued', 'running')
  AND EXISTS (
    SELECT 1
    FROM public.llm_run_events AS event
    WHERE event.run_id = run.run_id
      AND event.phase = 'regeneration_queued'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM public.llm_run_events AS event
    WHERE event.run_id = run.run_id
      AND event.phase <> 'regeneration_queued'
  );
