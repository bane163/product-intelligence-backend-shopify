-- Migration: create `documents` storage bucket
-- Created: 2025-12-19
-- This migration creates a private storage bucket named `documents`
-- intended for agent file uploads (Excel, PDF, images, generic blobs).

-- Insert bucket if it doesn't already exist
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
  'documents',
  'documents',
  false, -- private bucket
  52428800, -- 50 MiB in bytes
  ARRAY[
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-excel',
    'application/pdf',
    'image/png',
    'image/jpeg',
    'application/octet-stream'
  ]::text[]
)
ON CONFLICT (id) DO NOTHING;

-- Policies to allow the service role full access to objects in the documents bucket.
-- The backend uses the Supabase service role key for uploads and management.
DO $do$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policy p
    JOIN pg_class c ON p.polrelid = c.oid
    WHERE c.relname = 'objects' AND p.polname = 'service_role_full_access_documents'
  ) THEN
    EXECUTE $policy$
      CREATE POLICY service_role_full_access_documents
      ON storage.objects
      FOR ALL
      TO service_role
      USING (bucket_id = 'documents')
      WITH CHECK (bucket_id = 'documents');
    $policy$;
  END IF;
END $do$;

-- Optional: allow authenticated users to upload and read objects in this bucket.
-- These policies are intentionally permissive for convenience in many apps; please
-- tighten them (e.g., restrict by metadata.owner) if you need per-user isolation.
DO $do$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policy p
    JOIN pg_class c ON p.polrelid = c.oid
    WHERE c.relname = 'objects' AND p.polname = 'authenticated_upload_documents'
  ) THEN
    EXECUTE $policy$
      CREATE POLICY authenticated_upload_documents
      ON storage.objects
      FOR INSERT
      TO authenticated
      WITH CHECK (bucket_id = 'documents');
    $policy$;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policy p
    JOIN pg_class c ON p.polrelid = c.oid
    WHERE c.relname = 'objects' AND p.polname = 'authenticated_read_documents'
  ) THEN
    EXECUTE $policy$
      CREATE POLICY authenticated_read_documents
      ON storage.objects
      FOR SELECT
      TO authenticated
      USING (bucket_id = 'documents');
    $policy$;
  END IF;
END $do$;

-- Note: Depending on your Supabase version the internal storage schema may differ.
-- If these statements fail when applying, prefer using the Supabase CLI's `supabase storage` commands
-- or the Studio UI to create the bucket and policies. This SQL aims to be compatible with
-- typical self-hosted Supabase local setups.
