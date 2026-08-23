-- Preserve deterministic audit results when AI enhancement generation fails,
-- while exposing the failure to merchants instead of treating it as acceptance.
ALTER TABLE public.product_intelligence_audits
  ADD COLUMN IF NOT EXISTS enhancement_status TEXT NOT NULL DEFAULT 'legacy_unknown',
  ADD COLUMN IF NOT EXISTS enhancement_error_code TEXT,
  ADD COLUMN IF NOT EXISTS enhancement_error_message TEXT,
  ADD COLUMN IF NOT EXISTS enhancement_attempts INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS ai_suggestions_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS derived_suggestions_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE public.product_intelligence_audits
  DROP CONSTRAINT IF EXISTS product_intelligence_audits_enhancement_status_check;
ALTER TABLE public.product_intelligence_audits
  ADD CONSTRAINT product_intelligence_audits_enhancement_status_check
  CHECK (enhancement_status IN ('ready', 'failed', 'legacy_unknown'));

UPDATE public.product_intelligence_audits
SET enhancement_status = 'legacy_unknown'
WHERE enhancement_status IS NULL;

NOTIFY pgrst, 'reload schema';
