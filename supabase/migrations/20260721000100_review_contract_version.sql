alter table public.shopify_mutation_plans
  add column if not exists review_contract_version integer;

comment on column public.shopify_mutation_plans.review_contract_version is
  'Frontend/backend compatibility contract for field-level import review controls.';
