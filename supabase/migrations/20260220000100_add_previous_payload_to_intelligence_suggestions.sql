ALTER TABLE public.product_intelligence_suggestions
ADD COLUMN IF NOT EXISTS previous_payload JSONB NOT NULL DEFAULT '{}'::jsonb;
