CREATE TABLE IF NOT EXISTS public.llm_model_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_domain TEXT NOT NULL,
    name TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'ollama/openai-compat',
    base_url TEXT NOT NULL,
    model_id TEXT NOT NULL,
    version TEXT,
    api_key_ciphertext TEXT NOT NULL,
    api_key_last4 TEXT,
    temperature NUMERIC(3,2),
    max_tokens INTEGER,
    timeout_seconds INTEGER,
    is_active BOOLEAN NOT NULL DEFAULT false,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_model_configs_shop_created
    ON public.llm_model_configs(shop_domain, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_model_configs_shop_active
    ON public.llm_model_configs(shop_domain, is_active);
CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_model_configs_one_active_per_shop
    ON public.llm_model_configs(shop_domain)
    WHERE is_active = true;

ALTER TABLE public.llm_model_configs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policy
        WHERE polname = 'public_all_access_llm_model_configs'
        AND polrelid = 'public.llm_model_configs'::regclass
    ) THEN
        CREATE POLICY "public_all_access_llm_model_configs" ON public.llm_model_configs
        FOR ALL TO public
        USING (true)
        WITH CHECK (true);
    END IF;
END $$;
