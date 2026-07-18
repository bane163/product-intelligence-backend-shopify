-- Verified PDF provenance. Nullable columns preserve existing spreadsheet rows.
ALTER TABLE public.product_source_references
  ADD COLUMN IF NOT EXISTS anchor_id TEXT,
  ADD COLUMN IF NOT EXISTS document_kind TEXT,
  ADD COLUMN IF NOT EXISTS source_value TEXT,
  ADD COLUMN IF NOT EXISTS source_provider TEXT;

COMMENT ON COLUMN public.product_source_references.bounding_box IS
  'Displayed-page bbox [left, top, right, bottom], each coordinate normalized to 0..1';
