-- Migration: Setup documents bucket and file_metadata table
-- Created: 2025-12-24
-- Description: Creates storage bucket, metadata table, and permissive RLS for local dev.

-- 1. Create the 'documents' bucket
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'documents',
    'documents',
    false, -- private bucket, but we will add permissive policies
    52428800, -- 50 MiB
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

-- 2. Create the 'file_metadata' table
CREATE TABLE IF NOT EXISTS public.file_metadata (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    storage_path TEXT NOT NULL, -- The path used in storage (file_id)
    filename TEXT NOT NULL,
    content_type TEXT,
    size INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- 3. Enable RLS
ALTER TABLE public.file_metadata ENABLE ROW LEVEL SECURITY;

-- 4. Create Permissive Policies for 'file_metadata' (Public/Dev Access)
-- Allow unlimited access to everyone (anon and authenticated) for local dev simplicity
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policy WHERE polname = 'public_all_access_metadata' AND polrelid = 'public.file_metadata'::regclass
    ) THEN
        CREATE POLICY "public_all_access_metadata" ON public.file_metadata
        FOR ALL
        TO public
        USING (true)
        WITH CHECK (true);
    END IF;
END $$;

-- 5. Create Permissive Policies for 'storage.objects' (Documents Bucket)
-- We need to ensure existing policies don't conflict, but for now we just add a permissive one for this bucket.
-- Note: 'storage.objects' RLS is often tricky. We target the 'documents' bucket specifically.

DO $$
BEGIN
    -- Policy: Allow public to SELECT (download) from documents
    IF NOT EXISTS (
        SELECT 1 FROM pg_policy WHERE polname = 'public_select_documents' AND polrelid = 'storage.objects'::regclass
    ) THEN
        CREATE POLICY "public_select_documents" ON storage.objects
        FOR SELECT
        TO public
        USING (bucket_id = 'documents');
    END IF;

    -- Policy: Allow public to INSERT (upload) to documents
    IF NOT EXISTS (
        SELECT 1 FROM pg_policy WHERE polname = 'public_insert_documents' AND polrelid = 'storage.objects'::regclass
    ) THEN
        CREATE POLICY "public_insert_documents" ON storage.objects
        FOR INSERT
        TO public
        WITH CHECK (bucket_id = 'documents');
    END IF;

    -- Policy: Allow public to UPDATE documents
    IF NOT EXISTS (
        SELECT 1 FROM pg_policy WHERE polname = 'public_update_documents' AND polrelid = 'storage.objects'::regclass
    ) THEN
        CREATE POLICY "public_update_documents" ON storage.objects
        FOR UPDATE
        TO public
        USING (bucket_id = 'documents');
    END IF;

    -- Policy: Allow public to DELETE documents
    IF NOT EXISTS (
        SELECT 1 FROM pg_policy WHERE polname = 'public_delete_documents' AND polrelid = 'storage.objects'::regclass
    ) THEN
        CREATE POLICY "public_delete_documents" ON storage.objects
        FOR DELETE
        TO public
        USING (bucket_id = 'documents');
    END IF;
END $$;
