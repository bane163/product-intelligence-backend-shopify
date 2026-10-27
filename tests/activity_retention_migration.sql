-- Run with psql -v ON_ERROR_STOP=1 on a migrated local Supabase database.
-- All fixtures, migration changes and the scheduled job are rolled back.
BEGIN;
\ir ../supabase/migrations/20261027000100_activity_retention.sql

CREATE TEMP TABLE preservation_counts AS
SELECT (SELECT count(*) FROM public.submitted_documents) AS documents,
       (SELECT count(*) FROM public.product_intelligence_suggestions) AS suggestions,
       (SELECT count(*) FROM public.product_intelligence_audits) AS audits;

INSERT INTO public.llm_runs(run_id, status, shop_domain, created_at, ended_at) VALUES
 ('retention-test-expired', 'succeeded', 'retention-a.myshopify.com', now()-interval '90 days', now()-interval '30 days 1 microsecond'),
 ('retention-test-boundary', 'succeeded', 'retention-a.myshopify.com', now()-interval '90 days', now()-interval '30 days'),
 ('retention-test-recent', 'failed', 'retention-a.myshopify.com', now()-interval '90 days', now()),
 ('retention-test-legacy', 'cancelled', 'retention-a.myshopify.com', now()-interval '31 days', NULL),
 ('retention-test-running', 'running', 'retention-a.myshopify.com', now()-interval '90 days', NULL),
 ('retention-test-queued', 'queued', 'retention-a.myshopify.com', now()-interval '90 days', NULL),
 ('retention-test-active-job', 'failed', 'retention-a.myshopify.com', now()-interval '90 days', now()-interval '80 days'),
 ('retention-test-retryable', 'failed', 'retention-a.myshopify.com', now()-interval '90 days', now()-interval '80 days'),
 ('retention-test-other-tenant', 'succeeded', 'retention-b.myshopify.com', now()-interval '90 days', now()-interval '31 days');
INSERT INTO public.offload_jobs(job_id,job_type,run_id,status) VALUES
 ('retention-test-terminal-job', 'test', 'retention-test-expired', 'succeeded'),
 ('retention-test-active-job', 'test', 'retention-test-active-job', 'running'),
 ('retention-test-retryable-job', 'test', 'retention-test-retryable', 'retryable');
INSERT INTO public.llm_run_events(run_id,phase,message,seq) VALUES ('retention-test-expired','test','log',1);
INSERT INTO public.llm_run_messages(run_id,role,message,seq) VALUES ('retention-test-expired','user','message',1);
INSERT INTO public.billing_usage_events(run_id,shop_domain,billing_cycle_start,billing_cycle_end)
VALUES ('retention-test-expired','retention-a.myshopify.com',now()-interval '90 days',now()-interval '60 days');
INSERT INTO public.submitted_documents(submitted_id,name,import_mode)
VALUES ('retention-test-document','Retention test','create');
INSERT INTO public.product_intelligence_audits(audit_id,scope,status,overall_score,shop_domain,run_id)
VALUES ('retention-test-audit','catalog','success',90,'retention-a.myshopify.com','retention-test-expired');
INSERT INTO public.product_intelligence_suggestions(suggestion_id,audit_id,product_index,message,status,shop_domain,product_id,patch_payload,previous_payload)
VALUES ('retention-test-suggestion','retention-test-audit',0,'Test','applied','retention-a.myshopify.com','gid://shopify/Product/123','{"title":"New"}','{"title":"Old"}');

DO $$
DECLARE deleted integer;
BEGIN
  IF (SELECT count(*) FROM public.retained_activity_runs WHERE shop_domain='retention-a.myshopify.com' AND run_id LIKE 'retention-test-%') <> 6 THEN
    RAISE EXCEPTION 'Listing eligibility/boundary/active-work protection failed';
  END IF;
  IF EXISTS (SELECT 1 FROM public.retained_activity_runs WHERE run_id='retention-test-other-tenant') THEN
    RAISE EXCEPTION 'Other tenant expired run visible';
  END IF;
  deleted := public.cleanup_expired_activity();
  IF deleted <> 3 THEN RAISE EXCEPTION 'Expected 3 deletions, got %',deleted; END IF;
  IF EXISTS (SELECT 1 FROM public.llm_runs WHERE run_id IN ('retention-test-expired','retention-test-legacy','retention-test-other-tenant')) THEN
    RAISE EXCEPTION 'Expired runs survived';
  END IF;
  IF EXISTS (SELECT 1 FROM public.llm_run_events WHERE run_id='retention-test-expired')
     OR EXISTS (SELECT 1 FROM public.llm_run_messages WHERE run_id='retention-test-expired')
     OR EXISTS (SELECT 1 FROM public.offload_jobs WHERE run_id='retention-test-expired') THEN
    RAISE EXCEPTION 'Expired logs/jobs survived';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.billing_usage_events WHERE run_id='retention-test-expired') THEN
    RAISE EXCEPTION 'Billing history lost';
  END IF;
  IF (SELECT count(*) FROM public.submitted_documents) <> (SELECT documents+1 FROM preservation_counts)
     OR (SELECT count(*) FROM public.product_intelligence_suggestions) <> (SELECT suggestions+1 FROM preservation_counts)
     OR (SELECT count(*) FROM public.product_intelligence_audits) <> (SELECT audits+1 FROM preservation_counts) THEN
    RAISE EXCEPTION 'Documents or product history lost';
  END IF;
  IF public.cleanup_expired_activity() <> 0 THEN RAISE EXCEPTION 'Cleanup not idempotent'; END IF;
  IF has_function_privilege('authenticated','public.cleanup_expired_activity()','execute')
     OR has_function_privilege('anon','public.cleanup_expired_activity()','execute')
     OR NOT has_function_privilege('service_role','public.cleanup_expired_activity()','execute') THEN
    RAISE EXCEPTION 'Cleanup permissions incorrect';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='cleanup-expired-activity' AND schedule='0 * * * *') THEN
    RAISE EXCEPTION 'Hourly cleanup not installed';
  END IF;
END $$;
ROLLBACK;
