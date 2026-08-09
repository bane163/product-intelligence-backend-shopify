ALTER TABLE public.product_intelligence_suggestions
  ADD COLUMN IF NOT EXISTS value_origin TEXT NULL;

ALTER TABLE public.product_intelligence_suggestions
  DROP CONSTRAINT IF EXISTS product_intelligence_suggestions_value_origin_check;

-- Invalid legacy labels are not reliable provenance. Preserve that uncertainty as NULL.
UPDATE public.product_intelligence_suggestions
SET value_origin = NULL
WHERE value_origin IS NOT NULL
  AND value_origin NOT IN ('source', 'ai_assisted', 'merchant_edited', 'derived', 'unknown');

ALTER TABLE public.product_intelligence_suggestions
  ADD CONSTRAINT product_intelligence_suggestions_value_origin_check
  CHECK (
    value_origin IS NULL
    OR value_origin IN ('source', 'ai_assisted', 'merchant_edited', 'derived', 'unknown')
  );

-- Historical rows intentionally remain NULL: their provenance cannot be inferred safely.
NOTIFY pgrst, 'reload schema';
