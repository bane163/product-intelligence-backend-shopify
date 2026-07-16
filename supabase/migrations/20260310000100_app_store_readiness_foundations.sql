-- Migration: add durable Shopify auth state and token persistence for app-store readiness

CREATE TABLE IF NOT EXISTS public.shopify_app_tokens (
    shop_domain TEXT PRIMARY KEY,
    access_token_ciphertext TEXT NOT NULL,
    access_token_last4 TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_shopify_app_tokens_updated_at
    ON public.shopify_app_tokens(updated_at DESC);

CREATE TABLE IF NOT EXISTS public.shopify_oauth_states (
    state TEXT PRIMARY KEY,
    shop_domain TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_shopify_oauth_states_expires_at
    ON public.shopify_oauth_states(expires_at);

ALTER TABLE public.shopify_app_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.shopify_oauth_states ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS shopify_app_tokens_service_role_only ON public.shopify_app_tokens;
CREATE POLICY shopify_app_tokens_service_role_only
    ON public.shopify_app_tokens
    FOR ALL
    TO public
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

DROP POLICY IF EXISTS shopify_oauth_states_service_role_only ON public.shopify_oauth_states;
CREATE POLICY shopify_oauth_states_service_role_only
    ON public.shopify_oauth_states
    FOR ALL
    TO public
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
