-- Migration: Add observability correlation fields for API -> run -> worker tracing
-- Created: 2026-03-03

ALTER TABLE public.llm_runs
  ADD COLUMN IF NOT EXISTS request_id TEXT,
  ADD COLUMN IF NOT EXISTS correlation_id TEXT;

ALTER TABLE public.llm_run_events
  ADD COLUMN IF NOT EXISTS metadata JSONB,
  ADD COLUMN IF NOT EXISTS request_id TEXT,
  ADD COLUMN IF NOT EXISTS correlation_id TEXT;

ALTER TABLE public.offload_jobs
  ADD COLUMN IF NOT EXISTS request_id TEXT,
  ADD COLUMN IF NOT EXISTS correlation_id TEXT;

CREATE INDEX IF NOT EXISTS idx_llm_runs_request_id
  ON public.llm_runs(request_id);

CREATE INDEX IF NOT EXISTS idx_llm_runs_correlation_id
  ON public.llm_runs(correlation_id);

CREATE INDEX IF NOT EXISTS idx_llm_run_events_correlation_id
  ON public.llm_run_events(correlation_id);

CREATE INDEX IF NOT EXISTS idx_offload_jobs_correlation_id
  ON public.offload_jobs(correlation_id);

