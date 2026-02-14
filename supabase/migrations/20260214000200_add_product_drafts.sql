CREATE TABLE IF NOT EXISTS public.product_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    draft_id TEXT UNIQUE NOT NULL,
    run_id TEXT,
    import_mode TEXT NOT NULL,
    draft_name TEXT,
    product_count INTEGER NOT NULL DEFAULT 0,
    first_product_title TEXT,
    products JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_product_drafts_created_at ON public.product_drafts(created_at DESC);

ALTER TABLE public.product_drafts ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policy WHERE polname = 'public_all_access_product_drafts' AND polrelid = 'public.product_drafts'::regclass
    ) THEN
        CREATE POLICY "public_all_access_product_drafts" ON public.product_drafts
        FOR ALL TO public
        USING (true)
        WITH CHECK (true);
    END IF;
END $$;
