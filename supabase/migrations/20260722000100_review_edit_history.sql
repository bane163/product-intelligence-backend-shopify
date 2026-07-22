alter table public.product_draft_review_edits
  add column if not exists working_plan jsonb not null default '{}'::jsonb,
  add column if not exists grid_state jsonb not null default '{"filterValues":{"changes":"hide_deferred"},"expandedItemIds":[],"pagesByItem":{},"workspaceView":"split"}'::jsonb,
  add column if not exists history jsonb not null default '[]'::jsonb,
  add column if not exists history_cursor integer not null default 0 check (history_cursor >= 0),
  add column if not exists base_plan_id uuid references public.shopify_mutation_plans(plan_id) on delete set null;

comment on column public.product_draft_review_edits.working_plan is
  'Mutable review-plan snapshot for the active edit session.';

comment on column public.product_draft_review_edits.grid_state is
  'Durable filters, expansion, pagination, and workspace layout state.';

comment on column public.product_draft_review_edits.history is
  'Ordered durable review operations used by undo and redo.';

comment on column public.product_draft_review_edits.history_cursor is
  'Number of history operations currently applied; bounded by history length in the service.';

comment on column public.product_draft_review_edits.base_plan_id is
  'Mutation plan from which the working review plan was created.';
