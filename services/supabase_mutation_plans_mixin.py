"""Tenant-scoped persistence for versioned Shopify mutation plans."""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from application.domain.shopify_mutation_plan import normalize_review_plan_rows


class SupabaseMutationPlansMixin:
    mutation_plans: dict[str, dict[str, Any]]
    review_edit_sessions: dict[str, dict[str, Any]]

    @staticmethod
    def _default_review_grid_state() -> dict[str, Any]:
        return {
            "filterValues": {"changes": "hide_deferred"},
            "expandedItemIds": [],
            "pagesByItem": {},
        }

    @staticmethod
    def _session_field_errors(working_plan: dict[str, Any]) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []
        for item in working_plan.get("items", []):
            if not isinstance(item, dict):
                continue
            for row in item.get("review_changes", []):
                if not isinstance(row, dict):
                    continue
                value = row.get("proposed")
                field = str(row.get("field") or "")
                code = message = None
                if field in {"tags", "values"} and not isinstance(value, list):
                    code, message = "INVALID_LIST", "Enter one value per line, or revert this field."
                elif row.get("value_type") in {"money", "weight"} and field != "inventoryItem.weight.unit":
                    if value is None or value == "":
                        continue
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        number = -1
                    if number < 0 or number != number or number in {float("inf"), float("-inf")}:
                        code, message = "INVALID_DECIMAL", "Enter a finite number that is zero or greater."
                elif field == "title" and (not isinstance(value, str) or not value.strip()):
                    code, message = "REQUIRED", "This field is required."
                if code and message:
                    errors.append({
                        "change_id": str(row.get("change_id") or ""),
                        "value_path": str(row.get("value_path") or ""),
                        "code": code,
                        "message": message,
                    })
        return errors

    @classmethod
    def _hydrate_review_edit_session(
        cls, session: dict[str, Any]
    ) -> dict[str, Any]:
        """Add safe durable-state defaults to sessions created before history support."""
        hydrated = deepcopy(session)

        working_plan = hydrated.get("working_plan")
        hydrated["working_plan"] = normalize_review_plan_rows(
            working_plan if isinstance(working_plan, dict) else {}
        )

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
        hydrated["grid_state"] = grid_state

        raw_history = hydrated.get("history")
        unfiltered_history = deepcopy(raw_history) if isinstance(raw_history, list) else []
        raw_cursor = hydrated.get("history_cursor")
        old_cursor = (
            raw_cursor
            if isinstance(raw_cursor, int) and not isinstance(raw_cursor, bool)
            else len(unfiltered_history)
        )
        content_operations = {
            "value_edit",
            "value_revert",
            "option_values_edit",
            "selection",
        }
        history = [
            entry for entry in unfiltered_history
            if isinstance(entry, dict) and entry.get("operation") in content_operations
        ]
        hydrated["history"] = history
        cursor = sum(
            1 for entry in unfiltered_history[:max(0, old_cursor)]
            if isinstance(entry, dict) and entry.get("operation") in content_operations
        )
        hydrated["history_cursor"] = max(0, min(cursor, len(history)))
        hydrated["field_errors"] = cls._session_field_errors(hydrated["working_plan"])

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
        hydrated = normalize_review_plan_rows(plan)
        if hydrated.get("currency_code"):
            return hydrated
        items = (
            hydrated.get("items")
            if isinstance(hydrated.get("items"), list)
            else []
        )
        currency_code = next(
            (
                item.get("currency_code")
                for item in items
                if isinstance(item, dict) and item.get("currency_code")
            ),
            None,
        )
        return {
            **hydrated,
            "currency_code": str(currency_code or "USD").upper(),
        }

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
            persisted = {key: value for key, value in stored.items() if key != "field_errors"}
            client.table("product_draft_review_edits").upsert(
                persisted, on_conflict="session_id"
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
