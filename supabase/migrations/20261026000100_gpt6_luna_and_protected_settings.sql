INSERT INTO public.billing_rate_cards(version, provider, model, effective_at, rates)
VALUES ('openai:gpt-6-luna:v1', 'openai', 'gpt-6-luna', '2026-10-26T00:00:00Z',
  '{"input_per_million_usd":"0.10","cached_input_per_million_usd":"0.01","output_per_million_usd":"0.50","cache_write_multiplier":"1.25","long_context_threshold":272000,"long_input_multiplier":"2","long_output_multiplier":"1.5"}'::jsonb)
ON CONFLICT (version) DO NOTHING;

UPDATE public.llm_model_configs SET model_id = 'gpt-6-luna'
WHERE provider = 'openai' AND model_id = 'gpt-5.6-luna'
  AND extra ? 'seeded_by';

DROP POLICY IF EXISTS public_all_access_llm_model_configs ON public.llm_model_configs;
REVOKE ALL ON public.llm_model_configs FROM PUBLIC, anon, authenticated;

-- Column privileges preserve ordinary tenant access while reserving the threshold
-- for the backend service role. Revoke table privileges first: table-wide grants
-- would override column restrictions.
REVOKE INSERT, UPDATE ON public.product_intelligence_normalization_settings FROM PUBLIC, anon, authenticated;
GRANT INSERT (shop_domain, unit_system, locale_default_unit_system, categories) ON public.product_intelligence_normalization_settings TO authenticated;
GRANT UPDATE (unit_system, locale_default_unit_system, categories, updated_at) ON public.product_intelligence_normalization_settings TO authenticated;

CREATE OR REPLACE FUNCTION public.protect_normalization_confidence_threshold()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF current_user IN ('anon', 'authenticated') AND
     ((TG_OP = 'INSERT' AND NEW.confidence_threshold IS NOT NULL) OR
      (TG_OP = 'UPDATE' AND NEW.confidence_threshold IS DISTINCT FROM OLD.confidence_threshold)) THEN
    RAISE EXCEPTION 'confidence threshold requires service access';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS protect_normalization_confidence_threshold ON public.product_intelligence_normalization_settings;
CREATE TRIGGER protect_normalization_confidence_threshold
BEFORE INSERT OR UPDATE ON public.product_intelligence_normalization_settings
FOR EACH ROW EXECUTE FUNCTION public.protect_normalization_confidence_threshold();
