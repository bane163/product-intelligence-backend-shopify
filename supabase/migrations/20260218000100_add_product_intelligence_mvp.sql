CREATE TABLE IF NOT EXISTS public.product_intelligence_audits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_id TEXT UNIQUE NOT NULL,
    run_id TEXT,
    submitted_id TEXT,
    scope TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'success',
    overall_score INTEGER NOT NULL,
    findings_count INTEGER NOT NULL DEFAULT 0,
    component_scores JSONB NOT NULL DEFAULT '{}'::jsonb,
    totals JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE TABLE IF NOT EXISTS public.product_intelligence_findings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    finding_id TEXT UNIQUE NOT NULL,
    audit_id TEXT NOT NULL REFERENCES public.product_intelligence_audits(audit_id) ON DELETE CASCADE,
    product_index INTEGER NOT NULL,
    product_title TEXT,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    suggestion TEXT,
    field_path TEXT,
    patch_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE TABLE IF NOT EXISTS public.product_intelligence_suggestions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    suggestion_id TEXT UNIQUE NOT NULL,
    audit_id TEXT NOT NULL REFERENCES public.product_intelligence_audits(audit_id) ON DELETE CASCADE,
    finding_id TEXT,
    product_index INTEGER NOT NULL,
    product_title TEXT,
    category TEXT,
    severity TEXT,
    message TEXT NOT NULL,
    patch_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending',
    applied_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_product_intelligence_audits_created_at
    ON public.product_intelligence_audits(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_product_intelligence_findings_audit_id
    ON public.product_intelligence_findings(audit_id);
CREATE INDEX IF NOT EXISTS idx_product_intelligence_suggestions_audit_id
    ON public.product_intelligence_suggestions(audit_id);

ALTER TABLE public.product_intelligence_audits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.product_intelligence_findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.product_intelligence_suggestions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policy WHERE polname = 'public_all_access_product_intelligence_audits' AND polrelid = 'public.product_intelligence_audits'::regclass
    ) THEN
        CREATE POLICY "public_all_access_product_intelligence_audits" ON public.product_intelligence_audits
        FOR ALL TO public
        USING (true)
        WITH CHECK (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policy WHERE polname = 'public_all_access_product_intelligence_findings' AND polrelid = 'public.product_intelligence_findings'::regclass
    ) THEN
        CREATE POLICY "public_all_access_product_intelligence_findings" ON public.product_intelligence_findings
        FOR ALL TO public
        USING (true)
        WITH CHECK (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policy WHERE polname = 'public_all_access_product_intelligence_suggestions' AND polrelid = 'public.product_intelligence_suggestions'::regclass
    ) THEN
        CREATE POLICY "public_all_access_product_intelligence_suggestions" ON public.product_intelligence_suggestions
        FOR ALL TO public
        USING (true)
        WITH CHECK (true);
    END IF;
END $$;
