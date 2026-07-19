alter table public.product_drafts
  add column if not exists revision integer not null default 1 check (revision > 0);

create table if not exists public.shopify_mutation_plans (
  plan_id uuid primary key,
  draft_id text not null references public.product_drafts(draft_id) on delete cascade,
  shop_domain text not null,
  version integer not null check (version > 0),
  draft_revision integer not null check (draft_revision > 0),
  status text not null check (status in ('review', 'approved', 'stale', 'executing', 'completed', 'partially_applied', 'failed')),
  items jsonb not null default '[]'::jsonb,
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (draft_id, version)
);

create index if not exists shopify_mutation_plans_tenant_draft_idx
  on public.shopify_mutation_plans (shop_domain, draft_id, version desc);

alter table public.shopify_mutation_plans enable row level security;
