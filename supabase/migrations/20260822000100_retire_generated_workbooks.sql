-- Apply only after maintenance/purge_generated_workbooks.py --apply succeeds.

delete from public.product_draft_review_edits;

alter table public.product_draft_review_edits
  drop column if exists working_file_id;

alter table public.product_draft_review_edits
  drop constraint if exists product_draft_review_edits_surface_check;

alter table public.product_draft_review_edits
  add constraint product_draft_review_edits_surface_check
  check (surface = 'grid');

alter table public.product_drafts
  drop column if exists output_file_id,
  drop column if exists output_filename;

alter table public.llm_runs
  drop column if exists output_file_id,
  drop column if exists output_filename;
