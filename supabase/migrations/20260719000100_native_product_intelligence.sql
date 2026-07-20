-- Product-scoped intelligence is the source of truth for product detail surfaces.
ALTER TABLE public.product_intelligence_audits
  ADD COLUMN IF NOT EXISTS scoring_version TEXT NOT NULL DEFAULT 'legacy-catalog-v1';

ALTER TABLE public.product_intelligence_findings
  ADD COLUMN IF NOT EXISTS product_id TEXT;

CREATE TABLE IF NOT EXISTS public.product_intelligence_product_results (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  shop_domain TEXT NOT NULL,
  audit_id TEXT NOT NULL REFERENCES public.product_intelligence_audits(audit_id) ON DELETE CASCADE,
  product_id TEXT NOT NULL,
  product_title TEXT,
  score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
  component_scores JSONB NOT NULL DEFAULT '{}'::jsonb,
  findings_count INTEGER NOT NULL DEFAULT 0,
  audited_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  scoring_version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
  UNIQUE (shop_domain, audit_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_pi_product_results_latest
  ON public.product_intelligence_product_results(shop_domain, product_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pi_findings_product
  ON public.product_intelligence_findings(shop_domain, product_id, created_at DESC);

-- Best-effort finding backfill from the immutable product snapshot. Ambiguous rows
-- remain NULL and are deliberately not exposed as product-scoped findings.
UPDATE public.product_intelligence_findings f
SET product_id = snapshot.product_id
FROM (
  SELECT a.audit_id,
         (p.ordinality - 1)::integer AS product_index,
         p.value->>'id' AS product_id
  FROM public.product_intelligence_audits a
  CROSS JOIN LATERAL jsonb_array_elements(COALESCE(a.totals->'audited_products', '[]'::jsonb))
    WITH ORDINALITY AS p(value, ordinality)
  WHERE NULLIF(p.value->>'id', '') IS NOT NULL
) snapshot
WHERE f.audit_id = snapshot.audit_id
  AND f.product_index = snapshot.product_index
  AND f.product_id IS NULL;

ALTER TABLE public.product_intelligence_product_results ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "tenant_access_product_intelligence_product_results"
  ON public.product_intelligence_product_results;
CREATE POLICY "tenant_access_product_intelligence_product_results"
  ON public.product_intelligence_product_results
  FOR ALL TO public
  USING (shop_domain = current_setting('request.jwt.claims', true)::jsonb->>'shop_domain')
  WITH CHECK (shop_domain = current_setting('request.jwt.claims', true)::jsonb->>'shop_domain');

