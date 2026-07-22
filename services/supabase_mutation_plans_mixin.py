"""Tenant-scoped persistence for versioned Shopify mutation plans."""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


class SupabaseMutationPlansMixin:
    mutation_plans: dict[str, dict[str, Any]]
    review_edit_sessions: dict[str, dict[str, Any]]

    @staticmethod
    def _default_review_grid_state() -> dict[str, Any]:
        return {
            "filterValues": {"changes": "hide_deferred"},
            "expandedItemIds": [],
            "pagesByItem": {},
            "workspaceView": "split",
        }

    @classmethod
    def _hydrate_review_edit_session(
        cls, session: dict[str, Any]
    ) -> dict[str, Any]:
        """Add safe durable-state defaults to sessions created before history support."""
        hydrated = deepcopy(session)

        working_plan = hydrated.get("working_plan")
        hydrated["working_plan"] = working_plan if isinstance(working_plan, dict) else {}

        raw_grid_state = hydrated.get("grid_state")
        grid_state = cls._default_review_grid_state()
        if isinstance(raw_grid_state, dict):
            filter_values = raw_grid_state.get("filterValues")
            if isinstance(filter_values, dict):
                grid_state["filterValues"] = {
                    str(key): value for key, value in filter_values.items()
                }
            expanded_ids = raw_grid_state.get("expandedItemIds")
            if isinstance(expanded_ids, list):
                grid_state["expandedItemIds"] = list(
                    dict.fromkeys(item for item in expanded_ids if isinstance(item, str))
                )
            pages = raw_grid_state.get("pagesByItem")
            if isinstance(pages, dict):
                grid_state["pagesByItem"] = {
                    str(key): value
                    for key, value in pages.items()
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                }
            workspace_view = raw_grid_state.get("workspaceView")
            if workspace_view in {"split", "changes", "spreadsheet"}:
                grid_state["workspaceView"] = workspace_view
        hydrated["grid_state"] = grid_state

        raw_history = hydrated.get("history")
        history = deepcopy(raw_history) if isinstance(raw_history, list) else []
        hydrated["history"] = history
        raw_cursor = hydrated.get("history_cursor")
        cursor = (
            raw_cursor
            if isinstance(raw_cursor, int) and not isinstance(raw_cursor, bool)
            else len(history)
        )
        hydrated["history_cursor"] = max(0, min(cursor, len(history)))

        base_plan_id = hydrated.get("base_plan_id")
        if not isinstance(base_plan_id, str) or not base_plan_id:
            plan_id = hydrated["working_plan"].get("plan_id")
            base_plan_id = plan_id if isinstance(plan_id, str) and plan_id else None
        hydrated["base_plan_id"] = base_plan_id

        edit_revision = hydrated.get("edit_revision")
        if not isinstance(edit_revision, int) or isinstance(edit_revision, bool) or edit_revision < 1:
            hydrated["edit_revision"] = 1
        return hydrated

    def cleanup_expired_review_edit_sessions(self, *, shop_domain: str | None = None) -> int:
        """Expire stale sessions and remove their isolated workbook copies."""
        tenant = shop_domain.strip().lower() if shop_domain else None
        now = datetime.now(timezone.utc)
        expired: list[dict[str, Any]] = []
        for session in list(self.review_edit_sessions.values()):
            if session.get("status") != "active" or (tenant and session.get("shop_domain") != tenant):
                continue
            try:
                expires_at = datetime.fromisoformat(str(session.get("expires_at") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if expires_at <= now:
                expired.append(session)
        client = self._get_supabase_client()
        if client:
            query = client.table("product_draft_review_edits").select("*").eq("status", "active").lte("expires_at", now.isoformat())
            if tenant:
                query = query.eq("shop_domain", tenant)
            result = query.execute()
            for row in result.data or []:
                if isinstance(row, dict) and not any(item.get("session_id") == row.get("session_id") for item in expired):
                    expired.append(row)
        for session in expired:
            saved = {**session, "status": "expired", "updated_at": now.isoformat()}
            self.save_review_edit_session(saved)
            working_file_id = session.get("working_file_id")
            if isinstance(working_file_id, str) and working_file_id:
                self.delete_file(working_file_id)
        return len(expired)

    @staticmethod
    def _hydrate_plan(plan: dict[str, Any]) -> dict[str, Any]:
        if plan.get("currency_code"):
            return plan
        items = plan.get("items") if isinstance(plan.get("items"), list) else []
        currency_code = next(
            (
                item.get("currency_code")
                for item in items
                if isinstance(item, dict) and item.get("currency_code")
            ),
            None,
        )
        return {**plan, "currency_code": str(currency_code or "USD").upper()}

    def save_mutation_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        tenant = str(plan.get("shop_domain") or "").strip().lower()
        if not tenant:
            raise ValueError("Mutation plan requires shop_domain")
        sanitized_plan = {
            key: value for key, value in plan.items() if key != "execution_error"
        }
        plan = self._hydrate_plan({**sanitized_plan, "shop_domain": tenant})
        client = self._get_supabase_client()
        if client:
            # The table predates exact review data. Item-level JSONB persists
            # additive review fields (including the currency used to hydrate
            # the plan response) without a schema migration.
            persisted = {
                key: value for key, value in plan.items() if key != "currency_code"
            }
            client.table("shopify_mutation_plans").upsert(
                persisted, on_conflict="plan_id"
            ).execute()
        self.mutation_plans[str(plan["plan_id"])] = plan
        return plan

    def get_mutation_plan(
        self, plan_id: str, *, shop_domain: str
    ) -> dict[str, Any] | None:
        tenant = shop_domain.strip().lower()
        memory = self.mutation_plans.get(plan_id)
        if memory and memory.get("shop_domain") == tenant:
            return memory
        client = self._get_supabase_client()
        if client:
            result = (
                client.table("shopify_mutation_plans")
                .select("*")
                .eq("plan_id", plan_id)
                .eq("shop_domain", tenant)
                .limit(1)
                .execute()
            )
            if result.data:
                return self._hydrate_plan(result.data[0])
        return None

    def get_latest_mutation_plan(
        self, draft_id: str, *, shop_domain: str
    ) -> dict[str, Any] | None:
        tenant = shop_domain.strip().lower()
        candidates = [
            p
            for p in self.mutation_plans.values()
            if p.get("draft_id") == draft_id and p.get("shop_domain") == tenant
        ]
        if candidates:
            return max(
                candidates,
                key=lambda p: (int(p.get("version") or 0), p.get("created_at") or ""),
            )
        client = self._get_supabase_client()
        if client:
            result = (
                client.table("shopify_mutation_plans")
                .select("*")
                .eq("draft_id", draft_id)
                .eq("shop_domain", tenant)
                .order("version", desc=True)
                .limit(1)
                .execute()
            )
            if result.data:
                return self._hydrate_plan(result.data[0])
        return None

    def save_review_edit_session(self, session: dict[str, Any]) -> dict[str, Any]:
        tenant = str(session.get("shop_domain") or "").strip().lower()
        if not tenant:
            raise ValueError("Review edit session requires shop_domain")
        stored = self._hydrate_review_edit_session({**session, "shop_domain": tenant})
        client = self._get_supabase_client()
        if client:
            client.table("product_draft_review_edits").upsert(
                stored, on_conflict="session_id"
            ).execute()
        self.review_edit_sessions[str(stored["session_id"])] = stored
        return stored

    def get_review_edit_session(
        self, session_id: str, *, shop_domain: str
    ) -> dict[str, Any] | None:
        tenant = shop_domain.strip().lower()
        memory = self.review_edit_sessions.get(session_id)
        if memory and memory.get("shop_domain") == tenant:
            hydrated = self._hydrate_review_edit_session(memory)
            self.review_edit_sessions[session_id] = hydrated
            return hydrated
        client = self._get_supabase_client()
        if client:
            result = (
                client.table("product_draft_review_edits")
                .select("*")
                .eq("session_id", session_id)
                .eq("shop_domain", tenant)
                .limit(1)
                .execute()
            )
            if result.data:
                return self._hydrate_review_edit_session(result.data[0])
        return None

    def get_active_review_edit_session(
        self, draft_id: str, *, shop_domain: str
    ) -> dict[str, Any] | None:
        tenant = shop_domain.strip().lower()
        self.cleanup_expired_review_edit_sessions(shop_domain=tenant)
        candidates = [
            session
            for session in self.review_edit_sessions.values()
            if session.get("draft_id") == draft_id
            and session.get("shop_domain") == tenant
            and session.get("status") == "active"
        ]
        if candidates:
            session = max(candidates, key=lambda session: session.get("updated_at") or "")
            hydrated = self._hydrate_review_edit_session(session)
            self.review_edit_sessions[str(hydrated["session_id"])] = hydrated
            return hydrated
        client = self._get_supabase_client()
        if client:
            result = (
                client.table("product_draft_review_edits")
                .select("*")
                .eq("draft_id", draft_id)
                .eq("shop_domain", tenant)
                .eq("status", "active")
                .order("updated_at", desc=True)
                .limit(1)
                .execute()
            )
            if result.data:
                return self._hydrate_review_edit_session(result.data[0])
        return None
