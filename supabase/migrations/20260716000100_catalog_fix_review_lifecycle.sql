ALTER TABLE public.product_intelligence_suggestions
  ADD COLUMN IF NOT EXISTS product_id text,
  ADD COLUMN IF NOT EXISTS reverted_at timestamptz,
  ADD COLUMN IF NOT EXISTS superseded_at timestamptz,
  ADD COLUMN IF NOT EXISTS superseded_by_audit_id text,
  ADD COLUMN IF NOT EXISTS parent_suggestion_id text,
  ADD COLUMN IF NOT EXISTS root_suggestion_id text;

UPDATE public.product_intelligence_suggestions s
SET product_id = NULLIF(a.totals->'audited_products'->COALESCE(s.product_index, 0)->>'id', '')
FROM public.product_intelligence_audits a
WHERE a.audit_id = s.audit_id
  AND s.product_id IS NULL;

UPDATE public.product_intelligence_suggestions
SET root_suggestion_id = suggestion_id
WHERE root_suggestion_id IS NULL;

-- Legacy rows without a stable product identity cannot safely target Shopify.
UPDATE public.product_intelligence_suggestions
SET status = 'superseded', superseded_at = now(), updated_at = now()
WHERE status = 'pending' AND product_id IS NULL;

WITH ranked AS (
  SELECT s.suggestion_id,
         first_value(s.audit_id) OVER (
           PARTITION BY s.shop_domain, s.product_id
           ORDER BY a.created_at DESC, s.created_at DESC
         ) AS newest_audit_id
  FROM public.product_intelligence_suggestions s
  JOIN public.product_intelligence_audits a ON a.audit_id = s.audit_id
  WHERE s.status = 'pending' AND s.product_id IS NOT NULL AND a.status = 'success'
)
UPDATE public.product_intelligence_suggestions s
SET status = 'superseded', superseded_at = now(),
    superseded_by_audit_id = ranked.newest_audit_id, updated_at = now()
FROM ranked
WHERE ranked.suggestion_id = s.suggestion_id
  AND s.audit_id <> ranked.newest_audit_id;

ALTER TABLE public.product_intelligence_suggestions
  DROP CONSTRAINT IF EXISTS product_intelligence_suggestions_status_check;
ALTER TABLE public.product_intelligence_suggestions
  ADD CONSTRAINT product_intelligence_suggestions_status_check
  CHECK (status IN ('pending', 'applied', 'reverted', 'superseded'));

CREATE INDEX IF NOT EXISTS idx_intelligence_suggestions_product_lifecycle
  ON public.product_intelligence_suggestions(shop_domain, product_id, status, created_at DESC);
