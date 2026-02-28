-- Migration: tenant-scope drafts/submitted + realtime publication for workflow tables
-- Created: 2026-02-28

ALTER TABLE public.product_drafts
  ADD COLUMN IF NOT EXISTS shop_domain TEXT;

ALTER TABLE public.submitted_documents
  ADD COLUMN IF NOT EXISTS shop_domain TEXT;

UPDATE public.product_drafts pd
SET shop_domain = lower(r.shop_domain)
FROM public.llm_runs r
WHERE pd.shop_domain IS NULL
  AND r.shop_domain IS NOT NULL
  AND (
    r.run_id = pd.run_id
    OR r.run_id = pd.extraction_run_id
  );

UPDATE public.product_drafts pd
SET shop_domain = lower(oj.shop_domain)
FROM public.offload_jobs oj
WHERE pd.shop_domain IS NULL
  AND oj.shop_domain IS NOT NULL
  AND oj.draft_id = pd.draft_id::text;

UPDATE public.submitted_documents sd
SET shop_domain = lower(pd.shop_domain)
FROM public.product_drafts pd
WHERE sd.shop_domain IS NULL
  AND pd.shop_domain IS NOT NULL
  AND sd.draft_id = pd.draft_id::text;

UPDATE public.submitted_documents sd
SET shop_domain = lower(r.shop_domain)
FROM public.llm_runs r
WHERE sd.shop_domain IS NULL
  AND r.shop_domain IS NOT NULL
  AND sd.run_id = r.run_id;

CREATE INDEX IF NOT EXISTS idx_product_drafts_shop_domain_updated_at
  ON public.product_drafts(shop_domain, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_product_drafts_shop_domain_draft_id
  ON public.product_drafts(shop_domain, draft_id);

CREATE INDEX IF NOT EXISTS idx_product_drafts_shop_domain_extraction_run_id
  ON public.product_drafts(shop_domain, extraction_run_id);

CREATE INDEX IF NOT EXISTS idx_submitted_documents_shop_domain_submitted_at
  ON public.submitted_documents(shop_domain, submitted_at DESC);

DROP POLICY IF EXISTS "public_all_access_product_drafts" ON public.product_drafts;
DROP POLICY IF EXISTS product_drafts_tenant_access ON public.product_drafts;
CREATE POLICY product_drafts_tenant_access
  ON public.product_drafts
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

DROP POLICY IF EXISTS "public_all_access_submitted_documents" ON public.submitted_documents;
DROP POLICY IF EXISTS submitted_documents_tenant_access ON public.submitted_documents;
CREATE POLICY submitted_documents_tenant_access
  ON public.submitted_documents
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

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_publication
    WHERE pubname = 'supabase_realtime'
  ) THEN
    BEGIN
      ALTER PUBLICATION supabase_realtime ADD TABLE public.product_drafts;
    EXCEPTION
      WHEN duplicate_object THEN NULL;
    END;

    BEGIN
      ALTER PUBLICATION supabase_realtime ADD TABLE public.llm_runs;
    EXCEPTION
      WHEN duplicate_object THEN NULL;
    END;

    BEGIN
      ALTER PUBLICATION supabase_realtime ADD TABLE public.llm_run_events;
    EXCEPTION
      WHEN duplicate_object THEN NULL;
    END;
  END IF;
END $$;
