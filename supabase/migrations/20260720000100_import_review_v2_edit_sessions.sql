create table if not exists public.product_draft_review_edits (
  session_id uuid primary key,
  draft_id text not null references public.product_drafts(draft_id) on delete cascade,
  shop_domain text not null,
  surface text not null check (surface in ('grid', 'workbook')),
  base_draft_revision integer not null check (base_draft_revision > 0),
  edit_revision integer not null default 1 check (edit_revision > 0),
  pending_products jsonb not null default '[]'::jsonb,
  working_file_id text,
  status text not null check (status in ('active', 'applied', 'discarded', 'expired')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  expires_at timestamptz not null default (now() + interval '24 hours')
);

create unique index if not exists product_draft_review_edits_one_active_idx
  on public.product_draft_review_edits (shop_domain, draft_id)
  where status = 'active';

create index if not exists product_draft_review_edits_cleanup_idx
  on public.product_draft_review_edits (status, expires_at)
  where status = 'active';

alter table public.product_draft_review_edits enable row level security;

alter table public.submitted_documents
  add column if not exists approved_selection jsonb,
  add column if not exists value_provenance jsonb;

alter table public.shopify_mutation_plans
  add column if not exists approved_selection jsonb;

alter table public.file_metadata
  add column if not exists content_revision text;

alter table public.product_source_references
  add column if not exists value_path text;
