-- Migration: Add idempotency persistence for product intelligence bulk operations
-- Created: 2026-03-03

CREATE TABLE IF NOT EXISTS public.product_intelligence_bulk_operations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_domain TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'succeeded',
    response JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    CONSTRAINT product_intelligence_bulk_operations_unique_key
        UNIQUE (shop_domain, operation_type, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_product_intelligence_bulk_operations_lookup
    ON public.product_intelligence_bulk_operations(
        shop_domain,
        operation_type,
        idempotency_key
    );

ALTER TABLE public.product_intelligence_bulk_operations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_access_product_intelligence_bulk_operations
    ON public.product_intelligence_bulk_operations;

CREATE POLICY tenant_access_product_intelligence_bulk_operations
ON public.product_intelligence_bulk_operations
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
