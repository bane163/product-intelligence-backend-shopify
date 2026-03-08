-- Migration: add safe RPC helper to read Vault secrets by name
-- Notes:
-- - Uses dynamic SQL so migration still succeeds when Vault isn't enabled locally.
-- - Intended for backend/service_role usage only.

CREATE OR REPLACE FUNCTION public.get_vault_secret(p_name TEXT)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, vault
AS $$
DECLARE
    v_secret TEXT;
    v_name TEXT := NULLIF(trim(COALESCE(p_name, '')), '');
BEGIN
    IF v_name IS NULL THEN
        RETURN NULL;
    END IF;

    BEGIN
        EXECUTE $sql$
            SELECT ds.decrypted_secret
            FROM vault.decrypted_secrets ds
            WHERE ds.name = $1
            ORDER BY ds.updated_at DESC NULLS LAST, ds.created_at DESC NULLS LAST
            LIMIT 1
        $sql$
        INTO v_secret
        USING v_name;
    EXCEPTION
        WHEN undefined_table OR invalid_schema_name THEN
            RETURN NULL;
    END;

    RETURN v_secret;
END;
$$;

REVOKE ALL ON FUNCTION public.get_vault_secret(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.get_vault_secret(TEXT) FROM anon;
REVOKE ALL ON FUNCTION public.get_vault_secret(TEXT) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.get_vault_secret(TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.get_vault_secret(TEXT) TO postgres;
