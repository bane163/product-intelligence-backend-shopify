-- Repair partial regeneration children left pending when their pending parent was
-- never superseded. Completed and already-terminal lineages are intentionally untouched.
UPDATE public.product_intelligence_suggestions child
SET status = 'superseded', superseded_at = now(), updated_at = now()
FROM public.product_intelligence_suggestions parent
WHERE child.parent_suggestion_id = parent.suggestion_id
  AND child.shop_domain = parent.shop_domain
  AND child.status = 'pending'
  AND parent.status = 'pending';
