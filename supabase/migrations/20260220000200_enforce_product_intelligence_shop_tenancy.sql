ALTER TABLE IF EXISTS public.product_intelligence_audits
  ADD COLUMN IF NOT EXISTS shop_domain TEXT;

ALTER TABLE IF EXISTS public.product_intelligence_findings
  ADD COLUMN IF NOT EXISTS shop_domain TEXT;

ALTER TABLE IF EXISTS public.product_intelligence_suggestions
  ADD COLUMN IF NOT EXISTS shop_domain TEXT;

UPDATE public.product_intelligence_findings f
SET shop_domain = a.shop_domain
FROM public.product_intelligence_audits a
WHERE f.audit_id = a.audit_id
  AND f.shop_domain IS NULL
  AND a.shop_domain IS NOT NULL;

UPDATE public.product_intelligence_suggestions s
SET shop_domain = a.shop_domain
FROM public.product_intelligence_audits a
WHERE s.audit_id = a.audit_id
  AND s.shop_domain IS NULL
  AND a.shop_domain IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.product_intelligence_unscoped_quarantine (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_table TEXT NOT NULL,
  source_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  quarantined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_table, source_id)
);

INSERT INTO public.product_intelligence_unscoped_quarantine (source_table, source_id, payload)
SELECT 'product_intelligence_audits', audit_id, to_jsonb(a)
FROM public.product_intelligence_audits a
WHERE a.shop_domain IS NULL
ON CONFLICT (source_table, source_id) DO NOTHING;

INSERT INTO public.product_intelligence_unscoped_quarantine (source_table, source_id, payload)
SELECT 'product_intelligence_findings', finding_id, to_jsonb(f)
FROM public.product_intelligence_findings f
WHERE f.shop_domain IS NULL
ON CONFLICT (source_table, source_id) DO NOTHING;

INSERT INTO public.product_intelligence_unscoped_quarantine (source_table, source_id, payload)
SELECT 'product_intelligence_suggestions', suggestion_id, to_jsonb(s)
FROM public.product_intelligence_suggestions s
WHERE s.shop_domain IS NULL
ON CONFLICT (source_table, source_id) DO NOTHING;

DELETE FROM public.product_intelligence_findings WHERE shop_domain IS NULL;
DELETE FROM public.product_intelligence_suggestions WHERE shop_domain IS NULL;
DELETE FROM public.product_intelligence_audits WHERE shop_domain IS NULL;

ALTER TABLE public.product_intelligence_audits
  ALTER COLUMN shop_domain SET NOT NULL;

ALTER TABLE public.product_intelligence_findings
  ALTER COLUMN shop_domain SET NOT NULL;

ALTER TABLE public.product_intelligence_suggestions
  ALTER COLUMN shop_domain SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_product_intelligence_audits_shop_domain
  ON public.product_intelligence_audits(shop_domain);

CREATE INDEX IF NOT EXISTS idx_product_intelligence_findings_shop_domain
  ON public.product_intelligence_findings(shop_domain);

CREATE INDEX IF NOT EXISTS idx_product_intelligence_suggestions_shop_domain
  ON public.product_intelligence_suggestions(shop_domain);

DROP POLICY IF EXISTS "public_all_access_product_intelligence_audits" ON public.product_intelligence_audits;
DROP POLICY IF EXISTS "public_all_access_product_intelligence_findings" ON public.product_intelligence_findings;
DROP POLICY IF EXISTS "public_all_access_product_intelligence_suggestions" ON public.product_intelligence_suggestions;

DROP POLICY IF EXISTS "tenant_access_product_intelligence_audits" ON public.product_intelligence_audits;
DROP POLICY IF EXISTS "tenant_access_product_intelligence_findings" ON public.product_intelligence_findings;
DROP POLICY IF EXISTS "tenant_access_product_intelligence_suggestions" ON public.product_intelligence_suggestions;

CREATE POLICY "tenant_access_product_intelligence_audits"
ON public.product_intelligence_audits
FOR ALL TO authenticated
USING (shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', '')))
WITH CHECK (shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', '')));

CREATE POLICY "tenant_access_product_intelligence_findings"
ON public.product_intelligence_findings
FOR ALL TO authenticated
USING (shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', '')))
WITH CHECK (shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', '')));

CREATE POLICY "tenant_access_product_intelligence_suggestions"
ON public.product_intelligence_suggestions
FOR ALL TO authenticated
USING (shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', '')))
WITH CHECK (shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', '')));
