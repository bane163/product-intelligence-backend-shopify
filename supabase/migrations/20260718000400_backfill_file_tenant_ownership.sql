-- Repair only file ownership that is attributable to exactly one tenant.
-- Re-running is safe because owned rows are never changed.
with file_tenant_references as (
  select input_file_id as file_id, lower(trim(shop_domain)) as shop_domain
  from product_drafts where input_file_id is not null and shop_domain is not null
  union
  select output_file_id, lower(trim(shop_domain))
  from product_drafts where output_file_id is not null and shop_domain is not null
  union
  select input_file_id, lower(trim(shop_domain))
  from llm_runs where input_file_id is not null and shop_domain is not null
  union
  select output_file_id, lower(trim(shop_domain))
  from llm_runs where output_file_id is not null and shop_domain is not null
), uniquely_owned as (
  select file_id, min(shop_domain) as shop_domain
  from file_tenant_references
  where file_id is not null and shop_domain <> ''
  group by file_id
  having count(distinct shop_domain) = 1
)
update file_metadata as files
set shop_domain = owners.shop_domain
from uniquely_owned as owners
where files.storage_path = owners.file_id
  and (files.shop_domain is null or trim(files.shop_domain) = '');
