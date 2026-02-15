ALTER TABLE public.file_metadata
ADD COLUMN IF NOT EXISTS thumbnail_storage_path TEXT;

CREATE INDEX IF NOT EXISTS idx_file_metadata_thumbnail_storage_path
    ON public.file_metadata(thumbnail_storage_path);
