ALTER TABLE public.product_drafts
  ADD COLUMN IF NOT EXISTS input_file_id TEXT,
  ADD COLUMN IF NOT EXISTS input_filename TEXT,
  ADD COLUMN IF NOT EXISTS output_file_id TEXT,
  ADD COLUMN IF NOT EXISTS output_filename TEXT,
  ADD COLUMN IF NOT EXISTS extraction_status TEXT,
  ADD COLUMN IF NOT EXISTS extraction_run_id TEXT,
  ADD COLUMN IF NOT EXISTS extraction_error TEXT,
  ADD COLUMN IF NOT EXISTS submit_status TEXT,
  ADD COLUMN IF NOT EXISTS submit_run_id TEXT,
  ADD COLUMN IF NOT EXISTS submit_error TEXT;

CREATE INDEX IF NOT EXISTS idx_product_drafts_extraction_status
  ON public.product_drafts(extraction_status);

CREATE INDEX IF NOT EXISTS idx_product_drafts_submit_status
  ON public.product_drafts(submit_status);
