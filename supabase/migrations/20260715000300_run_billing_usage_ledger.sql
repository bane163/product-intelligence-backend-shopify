-- Idempotent run-based billing usage. Uploads never increment usage; only the
-- first successful document-import transition creates a ledger event.

ALTER TABLE public.merchant_subscriptions
  ADD COLUMN IF NOT EXISTS files_included integer NOT NULL DEFAULT 0;

ALTER TABLE public.llm_runs
  ADD COLUMN IF NOT EXISTS billing_mode text NOT NULL DEFAULT 'chargeable';

CREATE TABLE IF NOT EXISTS public.billing_usage_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id text NOT NULL UNIQUE REFERENCES public.llm_runs(run_id) ON DELETE CASCADE,
  shop_domain text NOT NULL,
  billing_cycle_start timestamptz NOT NULL,
  billing_cycle_end timestamptz NOT NULL,
  file_count integer NOT NULL DEFAULT 1 CHECK (file_count >= 0),
  token_count bigint NOT NULL DEFAULT 0 CHECK (token_count >= 0),
  is_backfill boolean NOT NULL DEFAULT false,
  is_billable boolean NOT NULL DEFAULT true,
  charge_amount numeric(10,2) NOT NULL DEFAULT 0,
  charge_status text NOT NULL DEFAULT 'not_required'
    CHECK (charge_status IN ('not_required', 'pending', 'submitted', 'failed')),
  shopify_idempotency_key text,
  charged_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS billing_usage_events_pending_idx
  ON public.billing_usage_events(shop_domain, billing_cycle_start, charge_status);

ALTER TABLE public.billing_usage_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS billing_usage_events_service_only ON public.billing_usage_events;
CREATE POLICY billing_usage_events_service_only ON public.billing_usage_events
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE OR REPLACE FUNCTION public.record_successful_import_usage()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  subscription public.merchant_subscriptions%ROWTYPE;
  cycle_usage public.usage_metrics%ROWTYPE;
  tokens bigint := greatest(coalesce(NEW.total_tokens, 0), 0);
  billable boolean := coalesce(NEW.billing_mode, 'chargeable') = 'chargeable';
  new_processed integer;
BEGIN
  IF NEW.status <> 'succeeded'
     OR OLD.status = 'succeeded'
     OR NEW.source NOT IN ('document_import', 'excel_import')
     OR NEW.shop_domain IS NULL THEN
    RETURN NEW;
  END IF;

  SELECT * INTO subscription FROM public.merchant_subscriptions
   WHERE shop_domain = NEW.shop_domain LIMIT 1;
  IF NOT FOUND THEN RETURN NEW; END IF;

  INSERT INTO public.usage_metrics (
    shop_domain, billing_cycle_start, billing_cycle_end, files_included,
    files_processed, overage_files, tokens_used
  ) VALUES (
    NEW.shop_domain, subscription.current_period_start,
    subscription.current_period_end, subscription.files_included, 0, 0, 0
  ) ON CONFLICT (shop_domain, billing_cycle_start) DO NOTHING;

  INSERT INTO public.billing_usage_events (
    run_id, shop_domain, billing_cycle_start, billing_cycle_end, token_count,
    is_billable, charge_status, shopify_idempotency_key
  ) VALUES (
    NEW.run_id, NEW.shop_domain, subscription.current_period_start,
    subscription.current_period_end, tokens, billable, 'not_required',
    encode(digest(NEW.shop_domain || ':' || subscription.current_period_start || ':' || NEW.run_id, 'sha256'), 'hex')
  ) ON CONFLICT (run_id) DO NOTHING;

  IF NOT FOUND THEN RETURN NEW; END IF;

  UPDATE public.usage_metrics SET
    files_processed = files_processed + 1,
    tokens_used = tokens_used + tokens,
    overage_files = greatest(0, files_processed + 1 - files_included),
    updated_at = now()
  WHERE shop_domain = NEW.shop_domain
    AND billing_cycle_start = subscription.current_period_start
  RETURNING * INTO cycle_usage;

  new_processed := cycle_usage.files_processed;
  IF billable AND new_processed > cycle_usage.files_included THEN
    UPDATE public.billing_usage_events SET
      charge_amount = least(0.85, greatest(0, 500 - cycle_usage.overage_charges_recorded)),
      charge_status = CASE WHEN cycle_usage.overage_charges_recorded < 500 THEN 'pending' ELSE 'not_required' END
    WHERE run_id = NEW.run_id;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS llm_runs_record_successful_import_usage ON public.llm_runs;
CREATE TRIGGER llm_runs_record_successful_import_usage
AFTER UPDATE OF status ON public.llm_runs
FOR EACH ROW EXECUTE FUNCTION public.record_successful_import_usage();

-- Cutover backfill is display-only and never enters the charge queue.
INSERT INTO public.billing_usage_events (
  run_id, shop_domain, billing_cycle_start, billing_cycle_end, token_count,
  is_backfill, is_billable, charge_status
)
SELECT r.run_id, r.shop_domain, s.current_period_start, s.current_period_end,
       greatest(coalesce(r.total_tokens, 0), 0), true, false, 'not_required'
FROM public.llm_runs r
JOIN public.merchant_subscriptions s USING (shop_domain)
WHERE r.status = 'succeeded'
  AND r.source IN ('document_import', 'excel_import')
  AND r.created_at >= s.current_period_start AND r.created_at < s.current_period_end
ON CONFLICT (run_id) DO NOTHING;
