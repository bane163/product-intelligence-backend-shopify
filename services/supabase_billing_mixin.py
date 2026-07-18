import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

LOG = logging.getLogger(__name__)


class SupabaseBillingMixin:
    # Provided by the concrete service (`SupabaseService.__init__`).
    _billing_store: dict[str, dict[str, Any]]

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
        """Check if a merchant can process files based on subscription status."""
        subscription = self.get_subscription(shop_domain)
        if not subscription:
            return False
        status = subscription.get("status")
        if status == "active":
            return True
        if status == "trial":
            trial_ends = subscription.get("trial_ends_at")
            if trial_ends:
                from datetime import datetime, timezone
                try:
                    end_dt = datetime.fromisoformat(trial_ends.replace("Z", "+00:00"))
                    return datetime.now(timezone.utc) < end_dt
                except (ValueError, TypeError):
                    return False
            return True  # No trial_ends_at set — allow (graceful)
        return False
