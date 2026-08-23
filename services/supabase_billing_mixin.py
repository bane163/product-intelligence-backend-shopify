import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from shared.credit_billing import PLANS
from .credit_billing_service import (
    BillingStorageUnavailable,
    CreditLimitReached,
    CreditOperationManager,
    Cycle,
)

LOG = logging.getLogger(__name__)


class SupabaseBillingMixin:
    # Provided by the concrete service (`SupabaseService.__init__`).
    _billing_store: dict[str, dict[str, Any]]
    _credit_manager: CreditOperationManager

    def _get_supabase_client(self) -> Optional[Any]:
        """Stub for static typing.

        Concrete service classes (e.g. `SupabaseService`) typically provide
        `_get_supabase_client` (see `SupabaseFileMixin`).  This stub exists so
        type checkers know the attribute is available on `self`.
        """
        raise NotImplementedError(
            "_get_supabase_client must be implemented by the host class"
        )

    def _utc_now(self) -> str:
        """Stub for typing — actual implementation provided by `SupabaseRunsMixin`."""
        raise NotImplementedError("_utc_now must be provided by the host class")

    # ------------------------------------------------------------------
    # merchant_subscriptions
    # ------------------------------------------------------------------

    def get_subscription(self, shop_domain: str) -> dict[str, Any] | None:
        client = self._get_supabase_client()
        if client:
            try:
                resp = (
                    client.table("merchant_subscriptions")
                    .select("*")
                    .eq("shop_domain", shop_domain)
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                if resp.data:
                    return resp.data[0]
                return None
            except Exception as exc:
                LOG.warning(
                    "Supabase query failed for merchant_subscriptions: %s", exc
                )
        return self._billing_store.get(f"sub:{shop_domain}")

    def upsert_subscription(
        self, shop_domain: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        now = self._utc_now()
        payload = {**data, "shop_domain": shop_domain, "updated_at": now}

        client = self._get_supabase_client()
        if client:
            try:
                client.table("merchant_subscriptions").upsert(
                    payload, on_conflict="shop_domain"
                ).execute()
                return payload
            except Exception as exc:
                LOG.warning(
                    "Supabase upsert failed for merchant_subscriptions: %s", exc
                )

        self._billing_store[f"sub:{shop_domain}"] = payload
        return payload

    # ------------------------------------------------------------------
    # usage_metrics — helpers
    # ------------------------------------------------------------------

    def _get_cycle_dates(
        self, shop_domain: str
    ) -> tuple[str, str, int]:
        """Return (billing_cycle_start, billing_cycle_end, files_included).

        Derives values from the shop's active subscription when available,
        otherwise falls back to a 30-day window starting now with 0 included
        files.
        """
        now = self._utc_now()
        subscription = self.get_subscription(shop_domain)
        if subscription:
            cycle_start = subscription.get("current_period_start") or now
            cycle_end = subscription.get("current_period_end")
            if not cycle_end:
                try:
                    start_dt = datetime.fromisoformat(str(cycle_start))
                except (ValueError, TypeError):
                    start_dt = datetime.now(timezone.utc)
                cycle_end = (start_dt + timedelta(days=30)).isoformat()
            files_included = subscription.get("files_included", 0)
        else:
            cycle_start = now
            start_dt = datetime.now(timezone.utc)
            cycle_end = (start_dt + timedelta(days=30)).isoformat()
            files_included = 0

        return str(cycle_start), str(cycle_end), int(files_included)

    # ------------------------------------------------------------------
    # usage_metrics
    # ------------------------------------------------------------------

    def get_current_usage(self, shop_domain: str) -> dict[str, Any] | None:
        client = self._get_supabase_client()
        if client:
            try:
                resp = (
                    client.table("usage_metrics")
                    .select("*")
                    .eq("shop_domain", shop_domain)
                    .lte("billing_cycle_start", self._utc_now())
                    .gt("billing_cycle_end", self._utc_now())
                    .limit(1)
                    .execute()
                )
                if resp.data:
                    return resp.data[0]
                return None
            except Exception as exc:
                LOG.warning("Supabase query failed for usage_metrics: %s", exc)
        return self._billing_store.get(f"usage:{shop_domain}")

    def increment_usage(
        self, shop_domain: str, *, files: int = 1, tokens: int = 0
    ) -> dict[str, Any]:
        now = self._utc_now()
        record = self.get_current_usage(shop_domain)

        client = self._get_supabase_client()
        if client:
            try:
                if record:
                    return self._increment_existing_usage(
                        client, record, files=files, tokens=tokens
                    )

                cycle_start, cycle_end, files_included = self._get_cycle_dates(
                    shop_domain
                )
                overage_files = max(0, files - files_included)
                new_record: dict[str, Any] = {
                    "shop_domain": shop_domain,
                    "billing_cycle_start": cycle_start,
                    "billing_cycle_end": cycle_end,
                    "files_included": files_included,
                    "files_processed": files,
                    "tokens_used": tokens,
                    "overage_files": overage_files,
                    "created_at": now,
                    "updated_at": now,
                }
                client.table("usage_metrics").insert(new_record).execute()
                return new_record
            except Exception as exc:
                LOG.warning("Supabase update failed for usage_metrics: %s", exc)

        # Fallback to in-memory
        key = f"usage:{shop_domain}"
        existing = self._billing_store.get(key, {})
        files_processed_new = existing.get("files_processed", 0) + files
        files_included = existing.get("files_included", 0)
        fallback: dict[str, Any] = {
            "shop_domain": shop_domain,
            "files_processed": files_processed_new,
            "tokens_used": existing.get("tokens_used", 0) + tokens,
            "overage_files": max(0, files_processed_new - files_included),
            "updated_at": now,
        }
        self._billing_store[key] = {**existing, **fallback}
        return self._billing_store[key]

    def _increment_existing_usage(
        self,
        client: Any,
        record: dict[str, Any],
        *,
        files: int,
        tokens: int,
        _retry: bool = False,
    ) -> dict[str, Any]:
        """Increment an existing usage record with optimistic-lock retry.

        Uses a WHERE guard on the previous ``files_processed`` value so that
        a concurrent writer doesn't silently overwrite changes.  If the
        guarded update touches 0 rows we re-read once and retry.
        """
        now = self._utc_now()
        old_files = record.get("files_processed", 0)
        files_processed_new = old_files + files
        files_included = record.get("files_included", 0)
        overage_files = max(0, files_processed_new - files_included)

        updated_fields: dict[str, Any] = {
            "files_processed": files_processed_new,
            "tokens_used": record.get("tokens_used", 0) + tokens,
            "overage_files": overage_files,
            "updated_at": now,
        }

        # Optimistic lock: only update if files_processed hasn't changed
        resp = (
            client.table("usage_metrics")
            .update(updated_fields)
            .eq("id", record["id"])
            .eq("files_processed", old_files)
            .execute()
        )

        if not resp.data and not _retry:
            # Another writer incremented between our read and update — retry once
            LOG.warning(
                "Optimistic lock miss for usage_metrics (shop=%s); retrying",
                record.get("shop_domain"),
            )
            fresh = self.get_current_usage(record["shop_domain"])
            if fresh:
                return self._increment_existing_usage(
                    client, fresh, files=files, tokens=tokens, _retry=True
                )

        return {**record, **updated_fields}

    # ------------------------------------------------------------------
    # billing_events
    # ------------------------------------------------------------------

    def record_billing_event(
        self, shop_domain: str, event_type: str, event_data: dict[str, Any]
    ) -> dict[str, Any]:
        now = self._utc_now()
        payload: dict[str, Any] = {
            "shop_domain": shop_domain,
            "event_type": event_type,
            "event_data": event_data,
            "created_at": now,
        }

        client = self._get_supabase_client()
        if client:
            try:
                client.table("billing_events").insert(payload).execute()
                return payload
            except Exception as exc:
                LOG.warning("Supabase insert failed for billing_events: %s", exc)

        self._billing_store[f"event:{shop_domain}:{now}"] = payload
        return payload

    # ------------------------------------------------------------------
    # Billing gate
    # ------------------------------------------------------------------

    def can_process(self, shop_domain: str) -> bool:
        """Check if a merchant has a currently active paid subscription."""
        subscription = self.get_subscription(shop_domain)
        if not subscription:
            return False
        return str(subscription.get("status") or "").lower() == "active"

    # ------------------------------------------------------------------
    # Processing credits (legacy file usage above remains audit-only)
    # ------------------------------------------------------------------

    @staticmethod
    def _plan_handle(subscription: dict[str, Any] | None) -> str:
        raw = str(
            (subscription or {}).get("plan_handle")
            or (subscription or {}).get("plan_name")
            or ""
        ).strip().lower()
        return raw if raw in PLANS else ""

    def get_credit_cycle(
        self,
        shop_domain: str,
        *,
        billing_mode: str = "shopify",
        plan_handle: str | None = None,
    ) -> Cycle:
        now = datetime.now(timezone.utc)
        subscription = self.get_subscription(shop_domain) or {}
        resolved_handle = (plan_handle or self._plan_handle(subscription)).lower()
        plan = PLANS.get(resolved_handle)
        if plan is None:
            raise ValueError("SUBSCRIPTION_REQUIRED")
        if billing_mode == "shopify":
            start = str(subscription.get("current_period_start") or now.isoformat())
            end = str(subscription.get("current_period_end") or (now + timedelta(days=30)).isoformat())
        else:
            start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            next_month = (start_dt.replace(day=28) + timedelta(days=4)).replace(day=1)
            start, end = start_dt.isoformat(), next_month.isoformat()
        return Cycle(
            shop_domain=shop_domain,
            starts_at=start,
            ends_at=end,
            included_credits=plan.included_credits,
            overage_limit_cents=int(subscription.get("overage_limit_cents") or 0),
            plan_handle=resolved_handle,
            billing_mode=billing_mode,
        )

    def reserve_credits(
        self,
        *,
        shop_domain: str,
        operation_key: str,
        operation_type: str,
        maximum_credits: int,
        rate_card_version: str,
        metadata: dict[str, Any] | None = None,
        operation_id: str | None = None,
        billing_mode: str = "shopify",
        plan_handle: str | None = None,
    ) -> dict[str, Any]:
        cycle = self.get_credit_cycle(shop_domain, billing_mode=billing_mode, plan_handle=plan_handle)
        client = self._get_supabase_client()
        if client:
            try:
                response = client.rpc(
                    "reserve_processing_credits",
                    {
                        "p_shop_domain": shop_domain,
                        "p_operation_key": operation_key,
                        "p_operation_type": operation_type,
                        "p_operation_id": operation_id,
                        "p_maximum_credits": int(maximum_credits),
                        "p_rate_card_version": rate_card_version,
                        "p_metadata": metadata or {},
                        "p_billing_mode": billing_mode,
                        "p_plan_handle": cycle.plan_handle,
                    },
                ).execute()
                data = response.data
                if isinstance(data, list) and data:
                    return data[0]
                if isinstance(data, dict):
                    return data
            except Exception as exc:
                raise BillingStorageUnavailable("Durable credit reservation is unavailable") from exc
        return self._credit_manager.reserve(
            cycle=cycle,
            operation_key=operation_key,
            operation_type=operation_type,
            maximum_credits=maximum_credits,
            rate_card_version=rate_card_version,
            metadata=metadata,
            operation_id=operation_id,
        )

    def record_provider_usage_event(self, **values: Any) -> dict[str, Any]:
        client = self._get_supabase_client()
        if client:
            try:
                payload = {
                    "operation_id": values["operation_id"],
                    "provider_call_key": values["provider_call_key"],
                    "provider": values["provider"],
                    "model": values.get("model"),
                    "usage": values.get("usage") or {},
                    "succeeded": values.get("succeeded", True),
                }
                response = client.table("provider_usage_events").upsert(
                    payload, on_conflict="operation_id,provider_call_key"
                ).execute()
                if response.data:
                    return response.data[0]
            except Exception as exc:
                raise BillingStorageUnavailable("Durable provider usage storage is unavailable") from exc
        return self._credit_manager.record_provider_usage(**values)

    def get_credit_operation(self, operation_id: str) -> dict[str, Any] | None:
        client = self._get_supabase_client()
        if client:
            try:
                response = client.table("billing_operations").select("*").eq(
                    "operation_id", operation_id
                ).limit(1).execute()
                if response.data:
                    return response.data[0]
            except Exception as exc:
                raise BillingStorageUnavailable("Durable billing operation storage is unavailable") from exc
        operation = self._credit_manager._operations.get(operation_id)
        return dict(operation) if operation else None

    def list_provider_usage_events(self, operation_id: str) -> list[dict[str, Any]]:
        client = self._get_supabase_client()
        if client:
            try:
                response = client.table("provider_usage_events").select("*").eq(
                    "operation_id", operation_id
                ).execute()
                return list(response.data or [])
            except Exception as exc:
                raise BillingStorageUnavailable("Durable provider usage storage is unavailable") from exc
        return [
            dict(item)
            for (candidate, _), item in self._credit_manager._usage_events.items()
            if candidate == operation_id
        ]

    def settle_credits(
        self,
        *,
        operation_id: str,
        actual_credits: int | None,
        raw_usage: dict[str, Any],
        cost_breakdown: dict[str, Any],
        missing_telemetry: bool = False,
    ) -> dict[str, Any]:
        client = self._get_supabase_client()
        if client:
            try:
                response = client.rpc(
                    "settle_processing_credits",
                    {
                        "p_operation_id": operation_id,
                        "p_actual_credits": actual_credits,
                        "p_raw_usage": raw_usage,
                        "p_cost_breakdown": cost_breakdown,
                        "p_missing_telemetry": missing_telemetry,
                    },
                ).execute()
                data = response.data
                if isinstance(data, list) and data:
                    return data[0]
                if isinstance(data, dict):
                    return data
            except Exception as exc:
                raise BillingStorageUnavailable("Durable credit settlement is unavailable") from exc
        operation = self.get_credit_operation(operation_id) or {}
        return self._credit_manager.settle(
            operation_id=operation_id,
            actual_credits=actual_credits,
            raw_usage=raw_usage,
            cost_breakdown=cost_breakdown,
            missing_telemetry=missing_telemetry,
            completion_cycle=self.get_credit_cycle(
                str(operation.get("shop_domain") or ""),
                billing_mode=str(operation.get("billing_mode") or "shopify"),
                plan_handle=str(operation.get("plan_handle") or "") or None,
            ),
        )

    def release_credits(self, operation_id: str, reason: str) -> dict[str, Any]:
        client = self._get_supabase_client()
        if client:
            try:
                response = client.rpc(
                    "release_processing_credits",
                    {"p_operation_id": operation_id, "p_reason": reason},
                ).execute()
                data = response.data
                if isinstance(data, list) and data:
                    return data[0]
                if isinstance(data, dict):
                    return data
            except Exception as exc:
                raise BillingStorageUnavailable("Durable credit release is unavailable") from exc
        return self._credit_manager.release(operation_id, reason)

    def reverse_credits(self, operation_id: str, credits: int, reason: str) -> dict[str, Any]:
        client = self._get_supabase_client()
        if client:
            response = client.rpc(
                "reverse_processing_credits",
                {"p_operation_id": operation_id, "p_credits": credits, "p_reason": reason},
            ).execute()
            data = response.data
            return data[0] if isinstance(data, list) and data else data
        return self._credit_manager.reverse(operation_id, credits, reason)

    def get_credit_summary(
        self,
        shop_domain: str,
        *,
        billing_mode: str = "shopify",
        plan_handle: str | None = None,
    ) -> dict[str, Any]:
        subscription = self.get_subscription(shop_domain) or {}
        cycle = self.get_credit_cycle(shop_domain, billing_mode=billing_mode, plan_handle=plan_handle)
        client = self._get_supabase_client()
        if client:
            try:
                response = client.rpc(
                    "processing_credit_summary", {
                        "p_shop_domain": shop_domain,
                        "p_billing_mode": billing_mode,
                        "p_plan_handle": cycle.plan_handle,
                    }
                ).execute()
                data = response.data
                if isinstance(data, list) and data:
                    return data[0]
                if isinstance(data, dict):
                    return data
            except Exception as exc:
                raise BillingStorageUnavailable("Durable credit summary is unavailable") from exc
        return self._credit_manager.summary(
            cycle,
            subscription_state=("active" if billing_mode == "simulated" else str(subscription.get("status") or "inactive")),
            billing_mode=billing_mode,
        )

    def set_overage_limit(
        self,
        shop_domain: str,
        cents: int,
        *,
        billing_mode: str = "shopify",
        plan_handle: str | None = None,
    ) -> dict[str, Any]:
        cycle = self.get_credit_cycle(shop_domain, billing_mode=billing_mode, plan_handle=plan_handle)
        client = self._get_supabase_client()
        if client:
            try:
                response = client.rpc(
                    "set_processing_credit_overage_limit",
                    {
                        "p_shop_domain": shop_domain,
                        "p_limit_cents": int(cents),
                        "p_billing_mode": billing_mode,
                        "p_plan_handle": cycle.plan_handle,
                    },
                ).execute()
                data = response.data
                if isinstance(data, list) and data:
                    return data[0]
                if isinstance(data, dict):
                    return data
            except Exception as exc:
                if "BILLING_LIMIT_CONFLICT" in str(exc):
                    raise CreditLimitReached(str(exc)) from exc
                raise
        result = self._credit_manager.set_overage_limit(cycle, cents)
        subscription = self.get_subscription(shop_domain)
        if billing_mode == "shopify" and subscription is not None:
            self.upsert_subscription(shop_domain, {**subscription, "overage_limit_cents": int(cents)})
        return result

    def pending_shopify_events(self, shop_domain: str | None = None) -> list[dict[str, Any]]:
        client = self._get_supabase_client()
        if client:
            query = client.table("shopify_event_outbox").select("*").in_("status", ["pending", "retry"])
            if shop_domain:
                query = query.eq("shop_domain", shop_domain)
            response = query.lte("next_attempt_at", self._utc_now()).order("created_at").limit(100).execute()
            return list(response.data or [])
        events = self._credit_manager.pending_outbox()
        if shop_domain:
            events = [event for event in events if event.get("shop_domain") == shop_domain]
        for event in events:
            subscription = self.get_subscription(str(event.get("shop_domain") or "")) or {}
            event.setdefault("shopify_shop_gid", subscription.get("shopify_shop_gid"))
        return events

    def mark_shopify_event(self, event_key: str, status: str, error: str | None = None) -> None:
        client = self._get_supabase_client()
        if client:
            now = datetime.now(timezone.utc)
            current = client.table("shopify_event_outbox").select("attempts").eq("event_key", event_key).limit(1).execute()
            attempts = int((current.data or [{}])[0].get("attempts") or 0) + 1
            values: dict[str, Any] = {
                "status": status,
                "attempts": attempts,
                "last_error": error,
                "next_attempt_at": (now + timedelta(seconds=min(2 ** attempts, 300))).isoformat(),
            }
            if status == "delivered":
                values["delivered_at"] = now.isoformat()
            client.table("shopify_event_outbox").update(values).eq("event_key", event_key).execute()
            return
        self._credit_manager.mark_outbox(event_key, status, error)
