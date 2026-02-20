ALTER TABLE public.product_intelligence_suggestions
ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS public.product_intelligence_normalization_settings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  shop_domain TEXT NOT NULL UNIQUE,
  unit_system TEXT NOT NULL DEFAULT 'metric',
  locale_default_unit_system TEXT,
  confidence_threshold DOUBLE PRECISION,
  categories JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

ALTER TABLE public.product_intelligence_normalization_settings
  ALTER COLUMN shop_domain SET NOT NULL;

ALTER TABLE public.product_intelligence_normalization_settings
  ALTER COLUMN unit_system SET DEFAULT 'metric';

ALTER TABLE public.product_intelligence_normalization_settings
  ALTER COLUMN categories SET DEFAULT '{}'::jsonb;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'chk_pi_norm_unit_system'
      AND conrelid = 'public.product_intelligence_normalization_settings'::regclass
  ) THEN
    ALTER TABLE public.product_intelligence_normalization_settings
      ADD CONSTRAINT chk_pi_norm_unit_system
      CHECK (unit_system IN ('metric', 'imperial'));
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'chk_pi_norm_locale_unit_system'
      AND conrelid = 'public.product_intelligence_normalization_settings'::regclass
  ) THEN
    ALTER TABLE public.product_intelligence_normalization_settings
      ADD CONSTRAINT chk_pi_norm_locale_unit_system
      CHECK (
        locale_default_unit_system IS NULL
        OR locale_default_unit_system IN ('metric', 'imperial')
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'chk_pi_norm_confidence_threshold'
      AND conrelid = 'public.product_intelligence_normalization_settings'::regclass
  ) THEN
    ALTER TABLE public.product_intelligence_normalization_settings
      ADD CONSTRAINT chk_pi_norm_confidence_threshold
      CHECK (
        confidence_threshold IS NULL
        OR (confidence_threshold >= 0 AND confidence_threshold <= 1)
      );
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_pi_normalization_settings_shop_domain
  ON public.product_intelligence_normalization_settings(shop_domain);

ALTER TABLE public.product_intelligence_normalization_settings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_access_product_intelligence_normalization_settings ON public.product_intelligence_normalization_settings;

CREATE POLICY tenant_access_product_intelligence_normalization_settings
ON public.product_intelligence_normalization_settings
FOR ALL TO authenticated
USING (shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', '')))
WITH CHECK (shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', '')));
