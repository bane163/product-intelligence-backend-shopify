-- Atomic workflow lifecycle, terminal precedence, and drift reconciliation.
ALTER TABLE public.offload_jobs ADD COLUMN IF NOT EXISTS failure_code TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_run_events_run_seq_unique
  ON public.llm_run_events(run_id, seq);

CREATE OR REPLACE FUNCTION public.next_run_event_seq(p_run_id TEXT)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE v_seq INTEGER;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(p_run_id, 0));
  SELECT COALESCE(max(seq), 0) + 1 INTO v_seq
  FROM public.llm_run_events WHERE run_id = p_run_id;
  RETURN v_seq;
END $$;

CREATE OR REPLACE FUNCTION public.cancel_run_cascade(p_run_id TEXT, p_shop_domain TEXT)
RETURNS SETOF public.llm_runs LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_run public.llm_runs%ROWTYPE; v_now TIMESTAMPTZ := timezone('utc', now());
BEGIN
  SELECT * INTO v_run FROM public.llm_runs
   WHERE run_id = p_run_id AND shop_domain = lower(trim(p_shop_domain)) FOR UPDATE;
  IF NOT FOUND OR v_run.status NOT IN ('queued', 'running') THEN RETURN; END IF;

  UPDATE public.llm_runs SET status='cancelled', ended_at=v_now, error=NULL,
    failure_code='cancelled_by_operator', failure_message='Run cancelled', resume_token=NULL
   WHERE run_id=p_run_id;
  UPDATE public.offload_jobs SET status='cancelled', error=NULL, failure_code=NULL,
    worker_id=NULL, claimed_at=NULL, claim_expires_at=NULL, updated_at=v_now
   WHERE run_id=p_run_id AND status NOT IN ('succeeded','failed','cancelled');
  UPDATE public.product_drafts SET
    extraction_status=CASE WHEN extraction_run_id=p_run_id THEN 'cancelled' ELSE extraction_status END,
    extraction_error=CASE WHEN extraction_run_id=p_run_id THEN NULL ELSE extraction_error END,
    submit_status=CASE WHEN submit_run_id=p_run_id THEN 'cancelled' ELSE submit_status END,
    submit_error=CASE WHEN submit_run_id=p_run_id THEN NULL ELSE submit_error END,
    extraction_progress=NULL, updated_at=v_now
   WHERE extraction_run_id=p_run_id OR submit_run_id=p_run_id;
  INSERT INTO public.llm_run_events(run_id,ts,phase,level,message,metadata,seq)
  VALUES(p_run_id,v_now,'run_cancelled','info','Run cancelled',jsonb_build_object('operation','cancel'),public.next_run_event_seq(p_run_id));
  RETURN QUERY SELECT * FROM public.llm_runs WHERE run_id=p_run_id;
END $$;

CREATE OR REPLACE FUNCTION public.transition_offload_workflow(
  p_job_id TEXT, p_target_status TEXT, p_error TEXT DEFAULT NULL,
  p_failure_code TEXT DEFAULT NULL, p_result JSONB DEFAULT NULL,
  p_available_at TIMESTAMPTZ DEFAULT NULL)
RETURNS SETOF public.offload_jobs LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE j public.offload_jobs%ROWTYPE; v_status TEXT:=lower(trim(p_target_status));
  v_run_status TEXT; v_now TIMESTAMPTZ:=timezone('utc',now());
BEGIN
  SELECT * INTO j FROM public.offload_jobs WHERE job_id=p_job_id FOR UPDATE;
  IF NOT FOUND THEN RETURN; END IF;
  IF j.status IN ('cancelled','succeeded','failed') AND j.status<>v_status THEN
    RETURN QUERY SELECT * FROM public.offload_jobs WHERE job_id=p_job_id; RETURN;
  END IF;
  v_run_status:=CASE WHEN v_status='retryable' THEN 'queued' WHEN v_status='claimed' THEN 'running' ELSE v_status END;
  UPDATE public.offload_jobs SET status=v_status, error=p_error, failure_code=p_failure_code,
    result=COALESCE(p_result,result), available_at=COALESCE(p_available_at,available_at),
    worker_id=CASE WHEN v_status IN ('retryable','succeeded','failed','cancelled') THEN NULL ELSE worker_id END,
    claimed_at=CASE WHEN v_status IN ('retryable','succeeded','failed','cancelled') THEN NULL ELSE claimed_at END,
    claim_expires_at=CASE WHEN v_status IN ('retryable','succeeded','failed','cancelled') THEN NULL ELSE claim_expires_at END,
    updated_at=v_now WHERE job_id=p_job_id;
  UPDATE public.llm_runs SET status=v_run_status,
    ended_at=CASE WHEN v_run_status IN ('succeeded','failed','cancelled') THEN v_now ELSE NULL END,
    duration_ms=CASE WHEN v_run_status IN ('queued','running') THEN NULL ELSE duration_ms END,
    error=CASE WHEN v_run_status='failed' THEN p_error ELSE NULL END,
    failure_code=CASE WHEN v_run_status='failed' THEN p_failure_code ELSE NULL END,
    failure_message=CASE WHEN v_run_status='failed' THEN p_error ELSE NULL END
   WHERE run_id=j.run_id AND status NOT IN ('cancelled','succeeded','failed');
  UPDATE public.product_drafts SET
    extraction_status=CASE WHEN j.job_type='document_import' THEN v_run_status ELSE extraction_status END,
    extraction_error=CASE WHEN j.job_type='document_import' AND v_run_status='failed' THEN p_error WHEN j.job_type='document_import' THEN NULL ELSE extraction_error END,
    submit_status=CASE WHEN j.job_type='shopify_submit' THEN v_run_status ELSE submit_status END,
    submit_error=CASE WHEN j.job_type='shopify_submit' AND v_run_status='failed' THEN p_error WHEN j.job_type='shopify_submit' THEN NULL ELSE submit_error END,
    extraction_progress=CASE WHEN j.job_type='document_import' AND v_run_status NOT IN ('queued','running') THEN NULL ELSE extraction_progress END,
    updated_at=v_now WHERE draft_id=j.draft_id;
  RETURN QUERY SELECT * FROM public.offload_jobs WHERE job_id=p_job_id;
END $$;

CREATE OR REPLACE FUNCTION public.sync_draft_extraction_progress() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  UPDATE public.product_drafts pd SET extraction_status=x.status,
    extraction_error=CASE WHEN x.status='failed' THEN COALESCE(x.error,'Extraction failed') ELSE NULL END,
    extraction_progress=CASE WHEN x.status IN ('queued','running') THEN pd.extraction_progress ELSE NULL END,
    updated_at=timezone('utc',now())
  FROM (SELECT DISTINCT ON (draft_id) draft_id,
      CASE WHEN status IN ('claimed','running') THEN 'running' ELSE status END status,error
    FROM public.offload_jobs WHERE job_type='document_import' AND draft_id IS NOT NULL
    ORDER BY draft_id,created_at DESC) x
  WHERE pd.draft_id=x.draft_id
    AND pd.extraction_status NOT IN ('cancelled','succeeded','failed')
    AND x.status IN ('queued','retryable','claimed','running','succeeded','failed','cancelled');
END $$;

-- Repair active-field drift without overriding the latest explicit control intent.
UPDATE public.llm_runs SET ended_at=NULL,duration_ms=NULL,error=NULL,failure_code=NULL,failure_message=NULL
 WHERE status IN ('queued','running');
