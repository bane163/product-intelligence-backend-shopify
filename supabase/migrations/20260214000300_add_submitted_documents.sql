CREATE TABLE IF NOT EXISTS public.submitted_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submitted_id TEXT UNIQUE NOT NULL,
    run_id TEXT,
    draft_id TEXT,
    name TEXT NOT NULL,
    import_mode TEXT NOT NULL,
    product_count INTEGER NOT NULL DEFAULT 0,
    products JSONB NOT NULL DEFAULT '[]'::jsonb,
    submitted_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_submitted_documents_submitted_at
    ON public.submitted_documents(submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_submitted_documents_name
    ON public.submitted_documents(name);

ALTER TABLE public.submitted_documents ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policy
        WHERE polname = 'public_all_access_submitted_documents'
          AND polrelid = 'public.submitted_documents'::regclass
    ) THEN
        CREATE POLICY "public_all_access_submitted_documents" ON public.submitted_documents
        FOR ALL TO public
        USING (true)
        WITH CHECK (true);
    END IF;
END $$;
