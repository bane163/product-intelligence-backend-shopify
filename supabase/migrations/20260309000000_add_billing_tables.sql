-- Migration: Add billing tables for merchant subscriptions, usage tracking, and billing events
-- Created: 2026-03-09

CREATE TABLE IF NOT EXISTS public.merchant_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_domain TEXT NOT NULL,
    shopify_subscription_id TEXT,
    shopify_usage_line_item_id TEXT,
    plan_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    trial_ends_at TIMESTAMPTZ,
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    CONSTRAINT uq_merchant_subscriptions_shop UNIQUE (shop_domain),
    CONSTRAINT merchant_subscriptions_status_check CHECK (
        status IN ('pending', 'active', 'frozen', 'cancelled', 'expired')
    )
);

CREATE INDEX IF NOT EXISTS idx_merchant_subscriptions_shop
    ON public.merchant_subscriptions(shop_domain);

CREATE INDEX IF NOT EXISTS idx_merchant_subscriptions_status
    ON public.merchant_subscriptions(status);

CREATE TABLE IF NOT EXISTS public.usage_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_domain TEXT NOT NULL,
    billing_cycle_start TIMESTAMPTZ NOT NULL,
    billing_cycle_end TIMESTAMPTZ NOT NULL,
    files_processed INTEGER NOT NULL DEFAULT 0,
    files_included INTEGER NOT NULL,
    overage_files INTEGER NOT NULL DEFAULT 0,
    overage_charges_recorded NUMERIC(10,2) NOT NULL DEFAULT 0,
    tokens_used BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    CONSTRAINT uq_usage_metrics_shop_cycle UNIQUE (shop_domain, billing_cycle_start)
);

CREATE INDEX IF NOT EXISTS idx_usage_metrics_shop
    ON public.usage_metrics(shop_domain);

CREATE INDEX IF NOT EXISTS idx_usage_metrics_cycle
    ON public.usage_metrics(shop_domain, billing_cycle_start, billing_cycle_end);

CREATE TABLE IF NOT EXISTS public.billing_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_domain TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_billing_events_shop
    ON public.billing_events(shop_domain);

CREATE INDEX IF NOT EXISTS idx_billing_events_type
    ON public.billing_events(event_type);

CREATE INDEX IF NOT EXISTS idx_billing_events_created
    ON public.billing_events(created_at);

ALTER TABLE public.merchant_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usage_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.billing_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS merchant_subscriptions_tenant_access ON public.merchant_subscriptions;
CREATE POLICY merchant_subscriptions_tenant_access
    ON public.merchant_subscriptions
    FOR ALL
    TO public
    USING (
        auth.role() = 'service_role'
        OR shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', ''))
    )
    WITH CHECK (
        auth.role() = 'service_role'
        OR shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', ''))
    );

DROP POLICY IF EXISTS usage_metrics_tenant_access ON public.usage_metrics;
CREATE POLICY usage_metrics_tenant_access
    ON public.usage_metrics
    FOR ALL
    TO public
    USING (
        auth.role() = 'service_role'
        OR shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', ''))
    )
    WITH CHECK (
        auth.role() = 'service_role'
        OR shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', ''))
    );

DROP POLICY IF EXISTS billing_events_tenant_access ON public.billing_events;
CREATE POLICY billing_events_tenant_access
    ON public.billing_events
    FOR ALL
    TO public
    USING (
        auth.role() = 'service_role'
        OR shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', ''))
    )
    WITH CHECK (
        auth.role() = 'service_role'
        OR shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', ''))
    );
