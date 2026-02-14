-- Migration: Add persistent LLM run logging tables
-- Created: 2026-02-14

CREATE TABLE IF NOT EXISTS public.llm_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL DEFAULT 'excel_import',
    status TEXT NOT NULL DEFAULT 'running',
    input_file_id TEXT,
    input_filename TEXT,
    input_content_type TEXT,
    input_size_bytes INTEGER,
    output_file_id TEXT,
    output_filename TEXT,
    prompt TEXT,
    writer_prompt TEXT,
    model_name TEXT,
    provider TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    duration_ms INTEGER,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    started_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    ended_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS public.llm_run_events (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES public.llm_runs(run_id) ON DELETE CASCADE,
    ts TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    phase TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    payload_preview TEXT,
    error TEXT,
    seq INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS public.llm_run_messages (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES public.llm_runs(run_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    message TEXT NOT NULL,
    meta JSONB DEFAULT '{}'::jsonb NOT NULL,
    seq INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_runs_created_at ON public.llm_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_runs_status_created_at ON public.llm_runs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_run_events_run_seq ON public.llm_run_events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_llm_run_messages_run_seq ON public.llm_run_messages(run_id, seq);

ALTER TABLE public.llm_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.llm_run_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.llm_run_messages ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policy WHERE polname = 'public_all_access_llm_runs' AND polrelid = 'public.llm_runs'::regclass
    ) THEN
        CREATE POLICY "public_all_access_llm_runs" ON public.llm_runs
        FOR ALL TO public
        USING (true)
        WITH CHECK (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policy WHERE polname = 'public_all_access_llm_run_events' AND polrelid = 'public.llm_run_events'::regclass
    ) THEN
        CREATE POLICY "public_all_access_llm_run_events" ON public.llm_run_events
        FOR ALL TO public
        USING (true)
        WITH CHECK (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policy WHERE polname = 'public_all_access_llm_run_messages' AND polrelid = 'public.llm_run_messages'::regclass
    ) THEN
        CREATE POLICY "public_all_access_llm_run_messages" ON public.llm_run_messages
        FOR ALL TO public
        USING (true)
        WITH CHECK (true);
    END IF;
END $$;
