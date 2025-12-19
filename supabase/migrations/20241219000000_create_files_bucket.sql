-- Create the files bucket for storing uploaded documents
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
  'files',
  'files',
  false,  -- private bucket
  52428800,  -- 50MB limit
  ARRAY['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 
        'application/vnd.ms-excel',
        'application/pdf',
        'image/png',
        'image/jpeg',
        'application/octet-stream']
)
ON CONFLICT (id) DO NOTHING;

-- Policy: Allow service role full access (for backend operations)
-- Since you're using SUPABASE_SERVICE_ROLE_KEY, this policy allows all operations
CREATE POLICY "Service role can do all operations on files bucket"
ON storage.objects
FOR ALL
TO service_role
USING (bucket_id = 'files')
WITH CHECK (bucket_id = 'files');

-- Optional: If you need authenticated users to upload/download their own files
-- CREATE POLICY "Users can upload files"
-- ON storage.objects
-- FOR INSERT
-- TO authenticated
-- WITH CHECK (bucket_id = 'files');

-- CREATE POLICY "Users can read their own files"
-- ON storage.objects
-- FOR SELECT
-- TO authenticated
-- USING (bucket_id = 'files' AND auth.uid()::text = (storage.foldername(name))[1]);

-- CREATE POLICY "Users can delete their own files"
-- ON storage.objects
-- FOR DELETE
-- TO authenticated
-- USING (bucket_id = 'files' AND auth.uid()::text = (storage.foldername(name))[1]);
