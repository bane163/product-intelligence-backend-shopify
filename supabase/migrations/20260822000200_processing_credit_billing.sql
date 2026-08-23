-- Cost-weighted processing credits. Legacy file events remain non-billable audit
-- data; there is intentionally no historical charge backfill.

DROP TRIGGER IF EXISTS llm_runs_record_successful_import_usage ON public.llm_runs;
COMMENT ON TABLE public.billing_usage_events IS
  'Legacy per-file audit events. Non-billable after processing-credit cutover.';
UPDATE public.billing_usage_events
SET is_billable = false, charge_status = 'not_required'
WHERE is_billable OR charge_status <> 'not_required';

ALTER TABLE public.merchant_subscriptions
  ADD COLUMN IF NOT EXISTS shopify_shop_gid text,
  ADD COLUMN IF NOT EXISTS plan_handle text,
  ADD COLUMN IF NOT EXISTS last_verified_at timestamptz,
  ADD COLUMN IF NOT EXISTS overage_limit_cents integer NOT NULL DEFAULT 0;

ALTER TABLE public.llm_runs
  ADD COLUMN IF NOT EXISTS billing_operation_id uuid,
  ADD COLUMN IF NOT EXISTS billing_quoted_credits integer,
  ADD COLUMN IF NOT EXISTS billing_rate_card_version text,
  ADD COLUMN IF NOT EXISTS billing_failure_class text;

ALTER TABLE public.merchant_subscriptions
  DROP CONSTRAINT IF EXISTS merchant_subscriptions_overage_limit_check;
ALTER TABLE public.merchant_subscriptions
  ADD CONSTRAINT merchant_subscriptions_overage_limit_check
  CHECK (overage_limit_cents BETWEEN 0 AND 50000);

CREATE TABLE public.billing_rate_cards (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  version text NOT NULL UNIQUE,
  provider text NOT NULL,
  model text,
  operation_type text,
  effective_at timestamptz NOT NULL,
  retired_at timestamptz,
  rates jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (retired_at IS NULL OR retired_at > effective_at)
);

INSERT INTO public.billing_rate_cards(version, provider, model, effective_at, rates)
VALUES (
  'openai:gpt-5.6-luna:v1', 'openai', 'gpt-5.6-luna', '2026-08-22T00:00:00Z',
  '{"input_per_million_usd":"0.20","cached_input_per_million_usd":"0.02","output_per_million_usd":"1.20","cache_write_multiplier":"1.25","long_context_threshold":272000,"long_input_multiplier":"2","long_output_multiplier":"1.5"}'::jsonb
) ON CONFLICT (version) DO NOTHING;

CREATE OR REPLACE FUNCTION public.prevent_rate_card_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'BILLING_RATE_CARDS_IMMUTABLE';
END $$;
DROP TRIGGER IF EXISTS billing_rate_cards_immutable ON public.billing_rate_cards;
CREATE TRIGGER billing_rate_cards_immutable
BEFORE UPDATE OR DELETE ON public.billing_rate_cards
FOR EACH ROW EXECUTE FUNCTION public.prevent_rate_card_mutation();

CREATE TABLE public.billing_cycles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  shop_domain text NOT NULL,
  starts_at timestamptz NOT NULL,
  ends_at timestamptz NOT NULL,
  plan_handle text NOT NULL CHECK (plan_handle IN ('starter','growth','scale')),
  included_credits integer NOT NULL CHECK (included_credits >= 0),
  overage_limit_cents integer NOT NULL DEFAULT 0 CHECK (overage_limit_cents BETWEEN 0 AND 50000),
  reserved_credits integer NOT NULL DEFAULT 0 CHECK (reserved_credits >= 0),
  settled_credits integer NOT NULL DEFAULT 0 CHECK (settled_credits >= 0),
  reversed_credits integer NOT NULL DEFAULT 0 CHECK (reversed_credits >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (shop_domain, starts_at),
  CHECK (ends_at > starts_at),
  CHECK (reversed_credits <= settled_credits)
);

CREATE TABLE public.billing_operations (
  operation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_key text NOT NULL,
  operation_type text NOT NULL,
  shop_domain text NOT NULL,
  cycle_id uuid NOT NULL REFERENCES public.billing_cycles(id),
  status text NOT NULL DEFAULT 'reserved'
    CHECK (status IN ('reserved','settled','released','reversed')),
  reserved_credits integer NOT NULL CHECK (reserved_credits >= 0),
  settled_credits integer NOT NULL DEFAULT 0 CHECK (settled_credits >= 0),
  reversed_credits integer NOT NULL DEFAULT 0 CHECK (reversed_credits >= 0),
  maximum_charge_cents integer NOT NULL CHECK (maximum_charge_cents >= 0),
  rate_card_version text NOT NULL REFERENCES public.billing_rate_cards(version),
  raw_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
  cost_breakdown jsonb NOT NULL DEFAULT '{}'::jsonb,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  missing_telemetry boolean NOT NULL DEFAULT false,
  release_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (shop_domain, operation_key),
  CHECK (settled_credits <= reserved_credits),
  CHECK (reversed_credits <= settled_credits)
);

CREATE TABLE public.provider_usage_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_id uuid NOT NULL REFERENCES public.billing_operations(operation_id),
  provider_call_key text NOT NULL,
  provider text NOT NULL,
  model text,
  usage jsonb NOT NULL,
  succeeded boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (operation_id, provider_call_key)
);

CREATE TABLE public.credit_ledger (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cycle_id uuid NOT NULL REFERENCES public.billing_cycles(id),
  operation_id uuid REFERENCES public.billing_operations(operation_id),
  entry_type text NOT NULL CHECK (entry_type IN ('reserve','settle','release','reverse','adjust')),
  credits integer NOT NULL,
  reason text,
  idempotency_key text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.shopify_event_outbox (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_id uuid NOT NULL REFERENCES public.billing_operations(operation_id),
  shop_domain text NOT NULL,
  shopify_shop_gid text,
  meter_handle text NOT NULL DEFAULT 'processing_credits',
  credits integer NOT NULL CHECK (credits <> 0),
  event_key text NOT NULL UNIQUE CHECK (length(event_key) <= 64),
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','retry','delivered','failed')),
  attempts integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  delivered_at timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX billing_cycles_current_idx ON public.billing_cycles(shop_domain, starts_at, ends_at);
CREATE INDEX billing_operations_cycle_idx ON public.billing_operations(cycle_id, status);
CREATE INDEX provider_usage_operation_idx ON public.provider_usage_events(operation_id);
CREATE INDEX shopify_event_outbox_pending_idx ON public.shopify_event_outbox(status, next_attempt_at);

ALTER TABLE public.billing_rate_cards ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.billing_cycles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.billing_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.provider_usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.credit_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.shopify_event_outbox ENABLE ROW LEVEL SECURITY;

CREATE POLICY billing_rate_cards_service_read ON public.billing_rate_cards
  FOR SELECT TO service_role USING (true);
CREATE POLICY billing_cycles_service_only ON public.billing_cycles
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY billing_operations_service_only ON public.billing_operations
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY provider_usage_events_service_only ON public.provider_usage_events
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY credit_ledger_service_only ON public.credit_ledger
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY shopify_event_outbox_service_only ON public.shopify_event_outbox
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE OR REPLACE FUNCTION public.plan_included_credits(p_handle text)
RETURNS integer LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE lower(p_handle)
    WHEN 'starter' THEN 1900 WHEN 'growth' THEN 5400 WHEN 'scale' THEN 17800
    ELSE NULL END
$$;

CREATE OR REPLACE FUNCTION public.ensure_processing_credit_cycle(p_shop_domain text)
RETURNS public.billing_cycles
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  sub public.merchant_subscriptions%ROWTYPE;
  result public.billing_cycles%ROWTYPE;
  handle text;
BEGIN
  SELECT * INTO sub FROM public.merchant_subscriptions
  WHERE shop_domain = p_shop_domain FOR UPDATE;
  IF NOT FOUND OR sub.status <> 'active' THEN
    RAISE EXCEPTION 'SUBSCRIPTION_REQUIRED' USING ERRCODE = 'P0001';
  END IF;
  handle := lower(coalesce(sub.plan_handle, sub.plan_name));
  IF public.plan_included_credits(handle) IS NULL
     OR sub.current_period_start IS NULL OR sub.current_period_end IS NULL THEN
    RAISE EXCEPTION 'SUBSCRIPTION_VERIFICATION_UNAVAILABLE' USING ERRCODE = 'P0001';
  END IF;
  INSERT INTO public.billing_cycles(
    shop_domain, starts_at, ends_at, plan_handle, included_credits, overage_limit_cents
  ) VALUES (
    p_shop_domain, sub.current_period_start, sub.current_period_end, handle,
    public.plan_included_credits(handle), sub.overage_limit_cents
  ) ON CONFLICT (shop_domain, starts_at) DO UPDATE SET
    ends_at = excluded.ends_at,
    plan_handle = excluded.plan_handle,
    included_credits = excluded.included_credits,
    overage_limit_cents = excluded.overage_limit_cents,
    updated_at = now()
  RETURNING * INTO result;
  RETURN result;
END $$;

CREATE OR REPLACE FUNCTION public.reserve_processing_credits(
  p_shop_domain text,
  p_operation_key text,
  p_operation_type text,
  p_maximum_credits integer,
  p_rate_card_version text,
  p_metadata jsonb DEFAULT '{}'::jsonb,
  p_operation_id uuid DEFAULT NULL
) RETURNS public.billing_operations
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  cycle public.billing_cycles%ROWTYPE;
  existing public.billing_operations%ROWTYPE;
  result public.billing_operations%ROWTYPE;
  committed integer;
BEGIN
  SELECT * INTO existing FROM public.billing_operations
  WHERE shop_domain = p_shop_domain AND operation_key = p_operation_key;
  IF FOUND THEN RETURN existing; END IF;
  cycle := public.ensure_processing_credit_cycle(p_shop_domain);
  SELECT * INTO cycle FROM public.billing_cycles WHERE id = cycle.id FOR UPDATE;
  committed := cycle.reserved_credits + cycle.settled_credits - cycle.reversed_credits;
  IF p_maximum_credits < 0 OR committed + p_maximum_credits > cycle.included_credits + cycle.overage_limit_cents THEN
    RAISE EXCEPTION 'CREDIT_LIMIT_REACHED' USING ERRCODE = 'P0001';
  END IF;
  INSERT INTO public.billing_operations(
    operation_id, operation_key, operation_type, shop_domain, cycle_id,
    reserved_credits, maximum_charge_cents, rate_card_version, metadata
  ) VALUES (
    coalesce(p_operation_id, gen_random_uuid()), p_operation_key, p_operation_type,
    p_shop_domain, cycle.id, p_maximum_credits, p_maximum_credits,
    p_rate_card_version, coalesce(p_metadata, '{}'::jsonb)
  ) RETURNING * INTO result;
  UPDATE public.billing_cycles SET reserved_credits = reserved_credits + p_maximum_credits,
    updated_at = now() WHERE id = cycle.id;
  INSERT INTO public.credit_ledger(cycle_id, operation_id, entry_type, credits, idempotency_key)
  VALUES (cycle.id, result.operation_id, 'reserve', p_maximum_credits, 'reserve:' || result.operation_id);
  RETURN result;
END $$;

CREATE OR REPLACE FUNCTION public.settle_processing_credits(
  p_operation_id uuid,
  p_actual_credits integer,
  p_raw_usage jsonb DEFAULT '{}'::jsonb,
  p_cost_breakdown jsonb DEFAULT '{}'::jsonb,
  p_missing_telemetry boolean DEFAULT false
) RETURNS public.billing_operations
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  op public.billing_operations%ROWTYPE;
  cycle public.billing_cycles%ROWTYPE;
  completion_cycle public.billing_cycles%ROWTYPE;
  amount integer;
  completion_capacity integer;
  app_shop_gid text;
BEGIN
  SELECT * INTO op FROM public.billing_operations WHERE operation_id = p_operation_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'BILLING_OPERATION_NOT_FOUND'; END IF;
  IF op.status <> 'reserved' THEN RETURN op; END IF;
  SELECT * INTO cycle FROM public.billing_cycles WHERE id = op.cycle_id FOR UPDATE;
  amount := least(op.reserved_credits,
    greatest(0, CASE WHEN p_missing_telemetry OR p_actual_credits IS NULL
      THEN op.reserved_credits ELSE p_actual_credits END));

  -- Usage belongs to the cycle in which processing completes. Release the old
  -- reservation and authorize only what the new cycle can cover; any excess is
  -- absorbed by the app while the merchant's original quote remains the ceiling.
  IF now() >= cycle.ends_at THEN
    completion_cycle := public.ensure_processing_credit_cycle(op.shop_domain);
    IF completion_cycle.id <> cycle.id THEN
      SELECT * INTO completion_cycle FROM public.billing_cycles
        WHERE id = completion_cycle.id FOR UPDATE;
      completion_capacity := completion_cycle.included_credits
        + completion_cycle.overage_limit_cents
        - completion_cycle.reserved_credits
        - completion_cycle.settled_credits
        + completion_cycle.reversed_credits;
      amount := least(amount, greatest(0, completion_capacity));
      UPDATE public.billing_cycles SET
        reserved_credits = reserved_credits - op.reserved_credits,
        updated_at = now() WHERE id = cycle.id;
      UPDATE public.billing_cycles SET settled_credits = settled_credits + amount,
        updated_at = now() WHERE id = completion_cycle.id;
      UPDATE public.billing_operations SET cycle_id = completion_cycle.id
        WHERE operation_id = p_operation_id;
      INSERT INTO public.credit_ledger(cycle_id, operation_id, entry_type, credits, reason, idempotency_key)
      VALUES (cycle.id, op.operation_id, 'release', -op.reserved_credits,
        'cycle_rollover', 'rollover-release:' || op.operation_id)
      ON CONFLICT (idempotency_key) DO NOTHING;
      cycle := completion_cycle;
    ELSE
      UPDATE public.billing_cycles SET
        reserved_credits = reserved_credits - op.reserved_credits,
        settled_credits = settled_credits + amount,
        updated_at = now() WHERE id = cycle.id;
    END IF;
  ELSE
    UPDATE public.billing_cycles SET
      reserved_credits = reserved_credits - op.reserved_credits,
      settled_credits = settled_credits + amount,
      updated_at = now() WHERE id = cycle.id;
  END IF;
  UPDATE public.billing_operations SET status = 'settled', settled_credits = amount,
    raw_usage = coalesce(p_raw_usage, '{}'::jsonb),
    cost_breakdown = coalesce(p_cost_breakdown, '{}'::jsonb),
    missing_telemetry = p_missing_telemetry, updated_at = now()
  WHERE operation_id = p_operation_id RETURNING * INTO op;
  INSERT INTO public.credit_ledger(cycle_id, operation_id, entry_type, credits, idempotency_key)
  VALUES (cycle.id, op.operation_id, 'settle', amount, 'settle:' || op.operation_id)
  ON CONFLICT (idempotency_key) DO NOTHING;
  IF amount > 0 THEN
    SELECT shopify_shop_gid INTO app_shop_gid FROM public.merchant_subscriptions
      WHERE shop_domain = op.shop_domain;
    INSERT INTO public.shopify_event_outbox(
      operation_id, shop_domain, shopify_shop_gid, credits, event_key, payload
    ) VALUES (
      op.operation_id, op.shop_domain, app_shop_gid, amount,
      'pc:v1:' || op.operation_id || ':s',
      jsonb_build_object('operation_id', op.operation_id, 'credits', amount)
    ) ON CONFLICT (event_key) DO NOTHING;
  END IF;
  RETURN op;
END $$;

CREATE OR REPLACE FUNCTION public.release_processing_credits(
  p_operation_id uuid, p_reason text
) RETURNS public.billing_operations
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE op public.billing_operations%ROWTYPE;
BEGIN
  SELECT * INTO op FROM public.billing_operations WHERE operation_id = p_operation_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'BILLING_OPERATION_NOT_FOUND'; END IF;
  IF op.status <> 'reserved' THEN RETURN op; END IF;
  UPDATE public.billing_cycles SET reserved_credits = reserved_credits - op.reserved_credits,
    updated_at = now() WHERE id = op.cycle_id;
  UPDATE public.billing_operations SET status = 'released', release_reason = p_reason,
    updated_at = now() WHERE operation_id = p_operation_id RETURNING * INTO op;
  INSERT INTO public.credit_ledger(cycle_id, operation_id, entry_type, credits, reason, idempotency_key)
  VALUES (op.cycle_id, op.operation_id, 'release', -op.reserved_credits, p_reason, 'release:' || op.operation_id)
  ON CONFLICT (idempotency_key) DO NOTHING;
  RETURN op;
END $$;

CREATE OR REPLACE FUNCTION public.reverse_processing_credits(
  p_operation_id uuid, p_credits integer, p_reason text
) RETURNS public.billing_operations
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE op public.billing_operations%ROWTYPE; amount integer; event_key text;
BEGIN
  SELECT * INTO op FROM public.billing_operations WHERE operation_id = p_operation_id FOR UPDATE;
  IF NOT FOUND OR op.status NOT IN ('settled','reversed') THEN
    RAISE EXCEPTION 'BILLING_OPERATION_NOT_SETTLED';
  END IF;
  amount := least(greatest(0, p_credits), op.settled_credits - op.reversed_credits);
  IF amount = 0 THEN RETURN op; END IF;
  UPDATE public.billing_cycles SET reversed_credits = reversed_credits + amount,
    updated_at = now() WHERE id = op.cycle_id;
  UPDATE public.billing_operations SET reversed_credits = reversed_credits + amount,
    status = CASE WHEN reversed_credits + amount = settled_credits THEN 'reversed' ELSE 'settled' END,
    updated_at = now() WHERE operation_id = p_operation_id RETURNING * INTO op;
  event_key := 'pc:v1:' || op.operation_id || ':r' || op.reversed_credits;
  INSERT INTO public.credit_ledger(cycle_id, operation_id, entry_type, credits, reason, idempotency_key)
  VALUES (op.cycle_id, op.operation_id, 'reverse', -amount, p_reason, event_key)
  ON CONFLICT (idempotency_key) DO NOTHING;
  INSERT INTO public.shopify_event_outbox(operation_id, shop_domain, shopify_shop_gid, credits, event_key, payload)
  SELECT op.operation_id, op.shop_domain, s.shopify_shop_gid, -amount, event_key,
    jsonb_build_object('operation_id', op.operation_id, 'credits', -amount)
  FROM public.merchant_subscriptions s WHERE s.shop_domain = op.shop_domain
  ON CONFLICT (event_key) DO NOTHING;
  RETURN op;
END $$;

CREATE OR REPLACE FUNCTION public.set_processing_credit_overage_limit(
  p_shop_domain text, p_limit_cents integer
) RETURNS public.billing_cycles
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE cycle public.billing_cycles%ROWTYPE; committed_overage integer;
BEGIN
  IF p_limit_cents NOT BETWEEN 0 AND 50000 THEN RAISE EXCEPTION 'BILLING_LIMIT_INVALID'; END IF;
  cycle := public.ensure_processing_credit_cycle(p_shop_domain);
  SELECT * INTO cycle FROM public.billing_cycles WHERE id = cycle.id FOR UPDATE;
  committed_overage := greatest(0,
    cycle.reserved_credits + cycle.settled_credits - cycle.reversed_credits - cycle.included_credits);
  IF p_limit_cents < committed_overage THEN RAISE EXCEPTION 'BILLING_LIMIT_CONFLICT'; END IF;
  UPDATE public.merchant_subscriptions SET overage_limit_cents = p_limit_cents,
    updated_at = now() WHERE shop_domain = p_shop_domain;
  UPDATE public.billing_cycles SET overage_limit_cents = p_limit_cents,
    updated_at = now() WHERE id = cycle.id RETURNING * INTO cycle;
  RETURN cycle;
END $$;

CREATE OR REPLACE FUNCTION public.processing_credit_summary(p_shop_domain text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE cycle public.billing_cycles%ROWTYPE; settled integer; committed integer;
BEGIN
  cycle := public.ensure_processing_credit_cycle(p_shop_domain);
  settled := cycle.settled_credits - cycle.reversed_credits;
  committed := settled + cycle.reserved_credits;
  RETURN jsonb_build_object(
    'subscription_state', 'active', 'billing_mode', 'shopify',
    'plan_handle', cycle.plan_handle, 'starts_at', cycle.starts_at, 'ends_at', cycle.ends_at,
    'included_credits', cycle.included_credits, 'settled_credits', settled,
    'reserved_credits', cycle.reserved_credits,
    'remaining_credits', greatest(0, cycle.included_credits - committed),
    'overage_spent_cents', greatest(0, settled - cycle.included_credits),
    'overage_committed_cents', greatest(0, committed - cycle.included_credits),
    'overage_limit_cents', cycle.overage_limit_cents,
    'history', coalesce((SELECT jsonb_agg(to_jsonb(o) ORDER BY o.created_at DESC)
      FROM public.billing_operations o WHERE o.cycle_id = cycle.id), '[]'::jsonb)
  );
END $$;
