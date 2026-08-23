-- Persist simulated/instrumentation usage alongside Shopify usage. Only
-- Shopify-mode settlements may enter the App Events outbox.

ALTER TABLE public.billing_cycles
  ADD COLUMN IF NOT EXISTS billing_mode text NOT NULL DEFAULT 'shopify'
    CHECK (billing_mode IN ('shopify', 'simulated', 'instrumentation'));

ALTER TABLE public.billing_operations
  ADD COLUMN IF NOT EXISTS billing_mode text NOT NULL DEFAULT 'shopify'
    CHECK (billing_mode IN ('shopify', 'simulated', 'instrumentation')),
  ADD COLUMN IF NOT EXISTS plan_handle text;

UPDATE public.billing_operations o
SET billing_mode = c.billing_mode,
    plan_handle = c.plan_handle
FROM public.billing_cycles c
WHERE c.id = o.cycle_id;

ALTER TABLE public.billing_cycles
  DROP CONSTRAINT IF EXISTS billing_cycles_shop_domain_starts_at_key;
ALTER TABLE public.billing_cycles
  DROP CONSTRAINT IF EXISTS billing_cycles_shop_mode_start_key;
ALTER TABLE public.billing_cycles
  ADD CONSTRAINT billing_cycles_shop_mode_start_key
  UNIQUE (shop_domain, billing_mode, starts_at);

DROP FUNCTION IF EXISTS public.processing_credit_summary(text);
DROP FUNCTION IF EXISTS public.set_processing_credit_overage_limit(text, integer);
DROP FUNCTION IF EXISTS public.reserve_processing_credits(text, text, text, integer, text, jsonb, uuid);
DROP FUNCTION IF EXISTS public.ensure_processing_credit_cycle(text);

CREATE FUNCTION public.ensure_processing_credit_cycle(
  p_shop_domain text,
  p_billing_mode text DEFAULT 'shopify',
  p_plan_handle text DEFAULT NULL
) RETURNS public.billing_cycles
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  sub public.merchant_subscriptions%ROWTYPE;
  result public.billing_cycles%ROWTYPE;
  handle text;
  cycle_start timestamptz;
  cycle_end timestamptz;
  limit_cents integer := 0;
BEGIN
  IF p_billing_mode NOT IN ('shopify', 'simulated', 'instrumentation') THEN
    RAISE EXCEPTION 'BILLING_MODE_INVALID' USING ERRCODE = 'P0001';
  END IF;

  IF p_billing_mode = 'shopify' THEN
    SELECT * INTO sub FROM public.merchant_subscriptions
    WHERE shop_domain = p_shop_domain FOR UPDATE;
    IF NOT FOUND OR sub.status <> 'active' THEN
      RAISE EXCEPTION 'SUBSCRIPTION_REQUIRED' USING ERRCODE = 'P0001';
    END IF;
    handle := lower(coalesce(sub.plan_handle, sub.plan_name));
    cycle_start := sub.current_period_start;
    cycle_end := sub.current_period_end;
    limit_cents := sub.overage_limit_cents;
    IF cycle_start IS NULL OR cycle_end IS NULL THEN
      RAISE EXCEPTION 'SUBSCRIPTION_VERIFICATION_UNAVAILABLE' USING ERRCODE = 'P0001';
    END IF;
  ELSE
    handle := lower(coalesce(p_plan_handle, 'starter'));
    cycle_start := date_trunc('month', now());
    cycle_end := cycle_start + interval '1 month';
  END IF;

  IF public.plan_included_credits(handle) IS NULL THEN
    RAISE EXCEPTION 'SUBSCRIPTION_REQUIRED' USING ERRCODE = 'P0001';
  END IF;

  INSERT INTO public.billing_cycles(
    shop_domain, billing_mode, starts_at, ends_at, plan_handle,
    included_credits, overage_limit_cents
  ) VALUES (
    p_shop_domain, p_billing_mode, cycle_start, cycle_end, handle,
    public.plan_included_credits(handle), limit_cents
  ) ON CONFLICT (shop_domain, billing_mode, starts_at) DO UPDATE SET
    ends_at = excluded.ends_at,
    plan_handle = excluded.plan_handle,
    included_credits = excluded.included_credits,
    overage_limit_cents = CASE
      WHEN excluded.billing_mode = 'shopify' THEN excluded.overage_limit_cents
      ELSE public.billing_cycles.overage_limit_cents
    END,
    updated_at = now()
  RETURNING * INTO result;
  RETURN result;
END $$;

CREATE FUNCTION public.reserve_processing_credits(
  p_shop_domain text,
  p_operation_key text,
  p_operation_type text,
  p_maximum_credits integer,
  p_rate_card_version text,
  p_metadata jsonb DEFAULT '{}'::jsonb,
  p_operation_id uuid DEFAULT NULL,
  p_billing_mode text DEFAULT 'shopify',
  p_plan_handle text DEFAULT NULL
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
  cycle := public.ensure_processing_credit_cycle(p_shop_domain, p_billing_mode, p_plan_handle);
  SELECT * INTO cycle FROM public.billing_cycles WHERE id = cycle.id FOR UPDATE;
  committed := cycle.reserved_credits + cycle.settled_credits - cycle.reversed_credits;
  IF p_maximum_credits < 0
     OR committed + p_maximum_credits > cycle.included_credits + cycle.overage_limit_cents THEN
    RAISE EXCEPTION 'CREDIT_LIMIT_REACHED' USING ERRCODE = 'P0001';
  END IF;
  INSERT INTO public.billing_operations(
    operation_id, operation_key, operation_type, shop_domain, cycle_id,
    billing_mode, plan_handle, reserved_credits, maximum_charge_cents,
    rate_card_version, metadata
  ) VALUES (
    coalesce(p_operation_id, gen_random_uuid()), p_operation_key, p_operation_type,
    p_shop_domain, cycle.id, cycle.billing_mode, cycle.plan_handle,
    p_maximum_credits, p_maximum_credits, p_rate_card_version,
    coalesce(p_metadata, '{}'::jsonb)
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
  IF p_actual_credits > op.reserved_credits THEN
    p_cost_breakdown := coalesce(p_cost_breakdown, '{}'::jsonb) || jsonb_build_object(
      'actual_credits', p_actual_credits,
      'absorbed_estimator_miss_credits', p_actual_credits - op.reserved_credits
    );
  END IF;

  IF now() >= cycle.ends_at THEN
    completion_cycle := public.ensure_processing_credit_cycle(
      op.shop_domain, op.billing_mode, op.plan_handle
    );
    IF completion_cycle.id <> cycle.id THEN
      SELECT * INTO completion_cycle FROM public.billing_cycles
        WHERE id = completion_cycle.id FOR UPDATE;
      completion_capacity := completion_cycle.included_credits
        + completion_cycle.overage_limit_cents
        - completion_cycle.reserved_credits
        - completion_cycle.settled_credits
        + completion_cycle.reversed_credits;
      amount := least(amount, greatest(0, completion_capacity));
      UPDATE public.billing_cycles SET reserved_credits = reserved_credits - op.reserved_credits,
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
      UPDATE public.billing_cycles SET reserved_credits = reserved_credits - op.reserved_credits,
        settled_credits = settled_credits + amount, updated_at = now() WHERE id = cycle.id;
    END IF;
  ELSE
    UPDATE public.billing_cycles SET reserved_credits = reserved_credits - op.reserved_credits,
      settled_credits = settled_credits + amount, updated_at = now() WHERE id = cycle.id;
  END IF;

  UPDATE public.billing_operations SET status = 'settled', settled_credits = amount,
    raw_usage = coalesce(p_raw_usage, '{}'::jsonb),
    cost_breakdown = coalesce(p_cost_breakdown, '{}'::jsonb),
    missing_telemetry = p_missing_telemetry, updated_at = now()
  WHERE operation_id = p_operation_id RETURNING * INTO op;
  INSERT INTO public.credit_ledger(cycle_id, operation_id, entry_type, credits, idempotency_key)
  VALUES (cycle.id, op.operation_id, 'settle', amount, 'settle:' || op.operation_id)
  ON CONFLICT (idempotency_key) DO NOTHING;

  IF amount > 0 AND op.billing_mode = 'shopify' THEN
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
  IF op.billing_mode = 'shopify' THEN
    INSERT INTO public.shopify_event_outbox(operation_id, shop_domain, shopify_shop_gid, credits, event_key, payload)
    SELECT op.operation_id, op.shop_domain, s.shopify_shop_gid, -amount, event_key,
      jsonb_build_object('operation_id', op.operation_id, 'credits', -amount)
    FROM public.merchant_subscriptions s WHERE s.shop_domain = op.shop_domain
    ON CONFLICT (event_key) DO NOTHING;
  END IF;
  RETURN op;
END $$;

CREATE FUNCTION public.set_processing_credit_overage_limit(
  p_shop_domain text,
  p_limit_cents integer,
  p_billing_mode text DEFAULT 'shopify',
  p_plan_handle text DEFAULT NULL
) RETURNS public.billing_cycles
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE cycle public.billing_cycles%ROWTYPE; committed_overage integer;
BEGIN
  IF p_limit_cents NOT BETWEEN 0 AND 50000 THEN RAISE EXCEPTION 'BILLING_LIMIT_INVALID'; END IF;
  cycle := public.ensure_processing_credit_cycle(p_shop_domain, p_billing_mode, p_plan_handle);
  SELECT * INTO cycle FROM public.billing_cycles WHERE id = cycle.id FOR UPDATE;
  committed_overage := greatest(0,
    cycle.reserved_credits + cycle.settled_credits - cycle.reversed_credits - cycle.included_credits);
  IF p_limit_cents < committed_overage THEN RAISE EXCEPTION 'BILLING_LIMIT_CONFLICT'; END IF;
  IF p_billing_mode = 'shopify' THEN
    UPDATE public.merchant_subscriptions SET overage_limit_cents = p_limit_cents,
      updated_at = now() WHERE shop_domain = p_shop_domain;
  END IF;
  UPDATE public.billing_cycles SET overage_limit_cents = p_limit_cents,
    updated_at = now() WHERE id = cycle.id RETURNING * INTO cycle;
  RETURN cycle;
END $$;

CREATE FUNCTION public.processing_credit_summary(
  p_shop_domain text,
  p_billing_mode text DEFAULT 'shopify',
  p_plan_handle text DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE cycle public.billing_cycles%ROWTYPE; settled integer; committed integer;
BEGIN
  cycle := public.ensure_processing_credit_cycle(p_shop_domain, p_billing_mode, p_plan_handle);
  settled := cycle.settled_credits - cycle.reversed_credits;
  committed := settled + cycle.reserved_credits;
  RETURN jsonb_build_object(
    'subscription_state', CASE WHEN p_billing_mode IN ('shopify','simulated') THEN 'active' ELSE 'inactive' END,
    'billing_mode', p_billing_mode,
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
