-- Migration: Allow retryable offload jobs to be claimed when available_at is due
-- Created: 2026-03-03

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
                (
                    status IN ('queued', 'retryable')
                    AND available_at <= timezone('utc'::text, now())
                )
             OR (
                    status = 'claimed'
                    AND claim_expires_at IS NOT NULL
                    AND claim_expires_at <= timezone('utc'::text, now())
                )
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
