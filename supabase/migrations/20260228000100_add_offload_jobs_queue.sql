-- Migration: Add durable offload jobs queue for import/submit processing
-- Created: 2026-02-28

CREATE TABLE IF NOT EXISTS public.offload_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id TEXT UNIQUE NOT NULL,
    queue_name TEXT NOT NULL DEFAULT 'default',
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 100,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    available_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    claimed_at TIMESTAMPTZ,
    claim_expires_at TIMESTAMPTZ,
    worker_id TEXT,
    run_id TEXT,
    draft_id TEXT,
    submitted_id TEXT,
    file_id TEXT,
    shop_domain TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    CONSTRAINT offload_jobs_status_check CHECK (
        status IN ('queued', 'claimed', 'running', 'succeeded', 'failed', 'cancelled', 'retryable')
    ),
    CONSTRAINT offload_jobs_attempts_check CHECK (
        attempt_count >= 0 AND max_attempts > 0
    ),
    CONSTRAINT offload_jobs_priority_check CHECK (priority >= 0)
);

CREATE INDEX IF NOT EXISTS idx_offload_jobs_queue_ready
    ON public.offload_jobs(queue_name, status, available_at, priority, created_at);

CREATE INDEX IF NOT EXISTS idx_offload_jobs_queued_lookup
    ON public.offload_jobs(queue_name, available_at, priority, created_at)
    WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS idx_offload_jobs_claim_expiry
    ON public.offload_jobs(claim_expires_at)
    WHERE status = 'claimed';

CREATE INDEX IF NOT EXISTS idx_offload_jobs_run_id
    ON public.offload_jobs(run_id);

CREATE INDEX IF NOT EXISTS idx_offload_jobs_draft_id
    ON public.offload_jobs(draft_id);

CREATE INDEX IF NOT EXISTS idx_offload_jobs_shop_domain_created_at
    ON public.offload_jobs(shop_domain, created_at DESC);

CREATE OR REPLACE FUNCTION public.claim_next_offload_job(
    p_queue_name TEXT DEFAULT 'default',
    p_worker_id TEXT DEFAULT NULL,
    p_lease_seconds INTEGER DEFAULT 300
)
RETURNS SETOF public.offload_jobs
LANGUAGE plpgsql
AS $$
DECLARE
    lease_seconds INTEGER := GREATEST(COALESCE(p_lease_seconds, 300), 1);
BEGIN
    RETURN QUERY
    WITH candidate AS (
        SELECT id
        FROM public.offload_jobs
        WHERE queue_name = COALESCE(NULLIF(trim(p_queue_name), ''), 'default')
          AND attempt_count < max_attempts
          AND (
                (status = 'queued' AND available_at <= timezone('utc'::text, now()))
             OR (status = 'claimed' AND claim_expires_at IS NOT NULL AND claim_expires_at <= timezone('utc'::text, now()))
          )
        ORDER BY priority ASC, available_at ASC, created_at ASC
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    UPDATE public.offload_jobs j
    SET status = 'claimed',
        claimed_at = timezone('utc'::text, now()),
        claim_expires_at = timezone('utc'::text, now()) + make_interval(secs => lease_seconds),
        worker_id = NULLIF(trim(COALESCE(p_worker_id, '')), ''),
        attempt_count = j.attempt_count + 1,
        updated_at = timezone('utc'::text, now())
    FROM candidate
    WHERE j.id = candidate.id
    RETURNING j.*;
END;
$$;

CREATE OR REPLACE FUNCTION public.update_offload_job_status(
    p_job_id TEXT,
    p_status TEXT,
    p_result JSONB DEFAULT NULL,
    p_error TEXT DEFAULT NULL,
    p_available_at TIMESTAMPTZ DEFAULT NULL
)
RETURNS SETOF public.offload_jobs
LANGUAGE plpgsql
AS $$
DECLARE
    normalized_status TEXT := lower(trim(COALESCE(p_status, '')));
BEGIN
    RETURN QUERY
    UPDATE public.offload_jobs j
    SET status = CASE WHEN normalized_status = '' THEN j.status ELSE normalized_status END,
        result = CASE WHEN p_result IS NULL THEN j.result ELSE p_result END,
        error = p_error,
        available_at = COALESCE(p_available_at, j.available_at),
        claimed_at = CASE
            WHEN normalized_status IN ('queued', 'retryable') THEN NULL
            ELSE j.claimed_at
        END,
        claim_expires_at = CASE
            WHEN normalized_status IN ('queued', 'retryable', 'succeeded', 'failed', 'cancelled') THEN NULL
            ELSE j.claim_expires_at
        END,
        updated_at = timezone('utc'::text, now())
    WHERE j.job_id = p_job_id
    RETURNING j.*;
END;
$$;

ALTER TABLE public.offload_jobs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS offload_jobs_tenant_access ON public.offload_jobs;
CREATE POLICY offload_jobs_tenant_access
    ON public.offload_jobs
    FOR ALL
    TO public
    USING (
        auth.role() = 'service_role'
        OR shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', ''))
        OR (shop_domain IS NULL AND coalesce(auth.jwt() ->> 'shop_domain', '') = '')
    )
    WITH CHECK (
        auth.role() = 'service_role'
        OR shop_domain = lower(coalesce(auth.jwt() ->> 'shop_domain', ''))
        OR (shop_domain IS NULL AND coalesce(auth.jwt() ->> 'shop_domain', '') = '')
    );
