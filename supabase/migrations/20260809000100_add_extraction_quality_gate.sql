alter table public.product_drafts
  add column if not exists quality_gate_status text,
  add column if not exists quality_report jsonb,
  add column if not exists quality_checked_at timestamptz;

alter table public.product_drafts
  drop constraint if exists product_drafts_quality_gate_status_check;

alter table public.product_drafts
  add constraint product_drafts_quality_gate_status_check
  check (
    quality_gate_status is null
    or quality_gate_status in ('pending', 'running', 'ready', 'live_validation_pending')
  );

comment on column public.product_drafts.quality_report is
  'Versioned deterministic and live post-extraction quality report shown during merchant review.';

alter table public.shopify_mutation_plans
  add column if not exists quality_summary jsonb,
  add column if not exists warnings_acknowledged_at timestamptz,
  add column if not exists warnings_acknowledgment_hash text;
