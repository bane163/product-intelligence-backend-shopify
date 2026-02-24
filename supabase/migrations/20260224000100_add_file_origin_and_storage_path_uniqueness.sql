ALTER TABLE public.file_metadata
  ADD COLUMN IF NOT EXISTS file_origin TEXT;

UPDATE public.file_metadata
SET file_origin = 'source_highlight'
WHERE file_origin IS NULL
  AND lower(coalesce(filename, '')) LIKE '%-source-highlight.%';

UPDATE public.file_metadata
SET file_origin = 'draft_resume'
WHERE file_origin IS NULL
  AND lower(coalesce(filename, '')) LIKE 'draft-%.xlsx';

UPDATE public.file_metadata
SET file_origin = 'submitted_resume'
WHERE file_origin IS NULL
  AND lower(coalesce(filename, '')) LIKE 'submitted-%.xlsx';

UPDATE public.file_metadata
SET file_origin = 'workflow_output'
WHERE file_origin IS NULL
  AND lower(coalesce(filename, '')) LIKE '%-products.xlsx';

UPDATE public.file_metadata
SET file_origin = 'merchant_upload'
WHERE file_origin IS NULL;

WITH ranked AS (
  SELECT
    id,
    storage_path,
    ROW_NUMBER() OVER (
      PARTITION BY storage_path
      ORDER BY created_at DESC, id DESC
    ) AS row_rank
  FROM public.file_metadata
)
DELETE FROM public.file_metadata fm
USING ranked r
WHERE fm.id = r.id
  AND r.row_rank > 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_file_metadata_storage_path_unique
  ON public.file_metadata(storage_path);

CREATE INDEX IF NOT EXISTS idx_file_metadata_file_origin
  ON public.file_metadata(file_origin);
