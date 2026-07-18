-- Retain malformed legacy drafts for audit history, but prevent application.
update public.product_intelligence_suggestions
set status = 'superseded',
    superseded_at = coalesce(superseded_at, now())
where status = 'pending'
  and patch_payload ? 'variant_operations'
  and (
    jsonb_typeof(patch_payload->'variant_operations') <> 'object'
    or jsonb_typeof(patch_payload->'variant_operations'->'create_options') <> 'array'
    or jsonb_array_length(coalesce(patch_payload->'variant_operations'->'create_options', '[]'::jsonb)) = 0
    or jsonb_typeof(patch_payload->'variant_operations'->'create_variants') <> 'array'
    or jsonb_array_length(coalesce(patch_payload->'variant_operations'->'create_variants', '[]'::jsonb)) = 0
    or exists (
      select 1 from jsonb_array_elements(coalesce(patch_payload->'variant_operations'->'create_variants', '[]'::jsonb)) variant
      where variant ? 'inventory_quantity'
         or jsonb_typeof(variant->'option_values') <> 'array'
         or jsonb_array_length(coalesce(variant->'option_values', '[]'::jsonb)) = 0
         or nullif(btrim(variant->>'sku'), '') is null
         or nullif(btrim(variant->>'price'), '') is null
    )
  );
