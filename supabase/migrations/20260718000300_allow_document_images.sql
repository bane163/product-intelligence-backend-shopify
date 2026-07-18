-- Idempotently extend the private documents bucket for supported standalone scans.
UPDATE storage.buckets
SET allowed_mime_types = ARRAY(
  SELECT DISTINCT mime
  FROM unnest(coalesce(allowed_mime_types, ARRAY[]::text[]) || ARRAY[
    'image/png', 'image/jpeg', 'image/webp'
  ]) AS mime
)
WHERE id = 'documents';
