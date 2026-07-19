"""Tenant-scoped persistence for versioned Shopify mutation plans."""

from typing import Any


class SupabaseMutationPlansMixin:
    mutation_plans: dict[str, dict[str, Any]]

    def save_mutation_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        tenant = str(plan.get("shop_domain") or "").strip().lower()
        if not tenant:
            raise ValueError("Mutation plan requires shop_domain")
        plan = {**plan, "shop_domain": tenant}
        client = self._get_supabase_client()
        if client:
            client.table("shopify_mutation_plans").upsert(plan, on_conflict="plan_id").execute()
        self.mutation_plans[str(plan["plan_id"])] = plan
        return plan

    def get_mutation_plan(self, plan_id: str, *, shop_domain: str) -> dict[str, Any] | None:
        tenant = shop_domain.strip().lower()
        memory = self.mutation_plans.get(plan_id)
        if memory and memory.get("shop_domain") == tenant:
            return memory
        client = self._get_supabase_client()
        if client:
            result = (client.table("shopify_mutation_plans").select("*")
                      .eq("plan_id", plan_id).eq("shop_domain", tenant).limit(1).execute())
            if result.data:
                return result.data[0]
        return None

    def get_latest_mutation_plan(self, draft_id: str, *, shop_domain: str) -> dict[str, Any] | None:
        tenant = shop_domain.strip().lower()
        candidates = [p for p in self.mutation_plans.values()
                      if p.get("draft_id") == draft_id and p.get("shop_domain") == tenant]
        if candidates:
            return max(candidates, key=lambda p: (int(p.get("version") or 0), p.get("created_at") or ""))
        client = self._get_supabase_client()
        if client:
            result = (client.table("shopify_mutation_plans").select("*")
                      .eq("draft_id", draft_id).eq("shop_domain", tenant)
                      .order("version", desc=True).limit(1).execute())
            if result.data:
                return result.data[0]
        return None
