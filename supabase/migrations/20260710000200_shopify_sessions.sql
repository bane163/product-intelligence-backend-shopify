CREATE TABLE IF NOT EXISTS public.shopify_sessions (
  id TEXT PRIMARY KEY,
  shop_domain TEXT NOT NULL,
  is_online BOOLEAN NOT NULL DEFAULT FALSE,
  expires_at TIMESTAMPTZ,
  session_ciphertext TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_shopify_sessions_shop
  ON public.shopify_sessions(shop_domain, is_online);
CREATE INDEX IF NOT EXISTS idx_shopify_sessions_expiry
  ON public.shopify_sessions(expires_at DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS public.merchant_user_preferences (
  shop_domain TEXT NOT NULL,
  user_id TEXT NOT NULL,
  locale TEXT NOT NULL DEFAULT 'en' CHECK (locale IN ('en', 'fr')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
  PRIMARY KEY (shop_domain, user_id)
);

ALTER TABLE public.shopify_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.merchant_user_preferences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS shopify_sessions_service_role_only ON public.shopify_sessions;
CREATE POLICY shopify_sessions_service_role_only ON public.shopify_sessions
  FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
DROP POLICY IF EXISTS merchant_user_preferences_service_role_only ON public.merchant_user_preferences;
CREATE POLICY merchant_user_preferences_service_role_only ON public.merchant_user_preferences
  FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

DROP TABLE IF EXISTS public.shopify_oauth_states;
DROP TABLE IF EXISTS public.shopify_app_tokens;
DROP SCHEMA IF EXISTS stockpile_app CASCADE;
