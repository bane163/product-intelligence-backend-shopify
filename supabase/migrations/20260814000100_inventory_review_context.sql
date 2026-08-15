alter table public.shopify_mutation_plans
  add column if not exists inventory_context jsonb;

comment on column public.shopify_mutation_plans.inventory_context is
  'Review contract v4 location selection and inventory readiness snapshot.';
