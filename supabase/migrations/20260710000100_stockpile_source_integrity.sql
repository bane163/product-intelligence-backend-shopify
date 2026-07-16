-- Stockpile production hardening: tenant-owned files, durable provenance, and
-- persisted event ordering. Existing unscoped files remain quarantined until
-- an operator assigns a shop; all new application writes must include a shop.

ALTER TABLE public.file_metadata
  ADD COLUMN IF NOT EXISTS shop_domain TEXT;

CREATE INDEX IF NOT EXISTS idx_file_metadata_shop_created
  ON public.file_metadata(shop_domain, created_at DESC);

CREATE TABLE IF NOT EXISTS public.product_source_references (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  shop_domain TEXT NOT NULL,
  draft_id TEXT REFERENCES public.product_drafts(draft_id) ON DELETE CASCADE,
  submitted_id TEXT,
  product_index INTEGER,
  field_name TEXT,
  source_file_id TEXT NOT NULL REFERENCES public.file_metadata(storage_path) ON DELETE RESTRICT,
  sheet_name TEXT,
  cell_range TEXT,
  page_number INTEGER,
  bounding_box JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
  CONSTRAINT product_source_reference_owner CHECK (draft_id IS NOT NULL OR submitted_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_product_source_refs_source
  ON public.product_source_references(shop_domain, source_file_id);
CREATE INDEX IF NOT EXISTS idx_product_source_refs_draft
  ON public.product_source_references(shop_domain, draft_id);

-- Older workers restarted event numbering for every retry. Preserve every event
-- while assigning a stable, monotonic sequence before enforcing uniqueness.
WITH ranked_events AS (
  SELECT id,
         row_number() OVER (
           PARTITION BY run_id
           ORDER BY ts ASC, id ASC
         )::INTEGER AS next_seq
  FROM public.llm_run_events
)
UPDATE public.llm_run_events AS event
SET seq = ranked_events.next_seq
FROM ranked_events
WHERE event.id = ranked_events.id
  AND event.seq IS DISTINCT FROM ranked_events.next_seq;

CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_run_events_run_seq_unique
  ON public.llm_run_events(run_id, seq);

ALTER TABLE public.product_source_references ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS public_all_access_metadata ON public.file_metadata;
DROP POLICY IF EXISTS file_metadata_tenant_access ON public.file_metadata;
CREATE POLICY file_metadata_tenant_access ON public.file_metadata
  FOR ALL TO authenticated
  USING (shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', '')))
  WITH CHECK (shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', '')));

DROP POLICY IF EXISTS product_source_references_tenant_access ON public.product_source_references;
CREATE POLICY product_source_references_tenant_access ON public.product_source_references
  FOR ALL TO authenticated
  USING (shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', '')))
  WITH CHECK (shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', '')));

DROP POLICY IF EXISTS public_select_documents ON storage.objects;
DROP POLICY IF EXISTS public_insert_documents ON storage.objects;
DROP POLICY IF EXISTS public_update_documents ON storage.objects;
DROP POLICY IF EXISTS public_delete_documents ON storage.objects;
