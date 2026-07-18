import logging
import uuid
from typing import Any, Optional

from .supabase_constants import NORMALIZATION_CATEGORY_KEYS

LOG = logging.getLogger(__name__)


class SupabaseIntelligenceMixin:
    def _get_supabase_client(self) -> Optional[Any]:
        """Stub for typing — actual implementation provided by host class (e.g. SupabaseFileMixin)."""
        raise NotImplementedError(
            "_get_supabase_client must be implemented by the host class"
        )

    def _bulk_operation_store(self) -> dict[str, dict[str, Any]]:
        store = getattr(self, "product_intelligence_bulk_operations", None)
        if isinstance(store, dict):
            return store
        fallback: dict[str, dict[str, Any]] = {}
        setattr(self, "product_intelligence_bulk_operations", fallback)
        return fallback

    def save_product_intelligence_audit(
        self,
        *,
        audit_id: str,
        run_id: str | None,
        submitted_id: str | None,
        scope: str,
        status: str,
        overall_score: int,
        findings_count: int,
        component_scores: dict[str, int],
        totals: dict[str, Any],
        shop_domain: str | None = None,
    ) -> dict[str, Any]:
        tenant = str(shop_domain or "").strip().lower()
        if not tenant:
            raise ValueError("Missing shop_domain for product intelligence audit")
        now = self._utc_now()
        payload = {
            "audit_id": audit_id,
            "run_id": run_id,
            "submitted_id": submitted_id,
            "scope": scope,
            "status": status,
            "overall_score": overall_score,
            "findings_count": findings_count,
            "component_scores": component_scores,
            "totals": totals,
            "shop_domain": tenant,
            "created_at": now,
            "updated_at": now,
        }
        client = self._get_supabase_client()
        if client:
            try:
                client.table("product_intelligence_audits").upsert(
                    payload, on_conflict="audit_id"
                ).execute()
                return payload
            except Exception:
                LOG.exception("Failed saving product intelligence audit %s", audit_id)
        self.product_intelligence_audits[audit_id] = payload
        return payload

    def _utc_now(self) -> str:
        """Stub for typing — actual implementation provided by `SupabaseRunsMixin`."""
        raise NotImplementedError("_utc_now must be provided by the host class")

    def save_product_intelligence_findings(
        self,
        *,
        audit_id: str,
        findings: list[dict[str, Any]],
        shop_domain: str | None = None,
    ) -> int:
        tenant = str(shop_domain or "").strip().lower()
        if not tenant:
            raise ValueError("Missing shop_domain for product intelligence findings")
        client = self._get_supabase_client()
        if client:
            try:
                client.table("product_intelligence_findings").delete().eq(
                    "audit_id", audit_id
                ).eq("shop_domain", tenant).execute()
                if findings:
                    payload = [
                        {**finding, "audit_id": audit_id, "shop_domain": tenant}
                        for finding in findings
                    ]
                    client.table("product_intelligence_findings").insert(
                        payload
                    ).execute()
                return len(findings)
            except Exception:
                LOG.exception(
                    "Failed saving intelligence findings for audit=%s", audit_id
                )
        self.product_intelligence_findings[audit_id] = [
            {**dict(item), "shop_domain": tenant} for item in findings
        ]
        return len(findings)

    def list_product_intelligence_audits(
        self,
        *,
        shop_domain: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        tenant = str(shop_domain or "").strip().lower()
        if not tenant:
            return []
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("product_intelligence_audits")
                    .select(
                        "audit_id,submitted_id,status,overall_score,findings_count,totals,run_id,created_at"
                    )
                    .eq("shop_domain", tenant)
                    .order("created_at", desc=True)
                    .range(offset, offset + limit - 1)
                    .execute()
                )
                return res.data or []
            except Exception:
                LOG.exception("Failed listing product intelligence audits")
        audits = [
            dict(item)
            for item in self.product_intelligence_audits.values()
            if str(item.get("shop_domain") or "").strip().lower() == tenant
        ]
        audits.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return audits[offset : offset + limit]

    def list_product_intelligence_audit_artifacts(
        self,
        *,
        audit_ids: list[str],
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        tenant = str(shop_domain or "").strip().lower()
        if not tenant:
            return []

        normalized_audit_ids: list[str] = []
        seen_ids: set[str] = set()
        for audit_id in audit_ids:
            normalized_id = str(audit_id or "").strip()
            if not normalized_id or normalized_id in seen_ids:
                continue
            seen_ids.add(normalized_id)
            normalized_audit_ids.append(normalized_id)
        if not normalized_audit_ids:
            return []

        client = self._get_supabase_client()
        if client:
            try:
                audits_res = (
                    client.table("product_intelligence_audits")
                    .select(
                        "audit_id,run_id,submitted_id,status,overall_score,findings_count,component_scores,totals,created_at"
                    )
                    .eq("shop_domain", tenant)
                    .in_("audit_id", normalized_audit_ids)
                    .execute()
                )
                findings_res = (
                    client.table("product_intelligence_findings")
                    .select("*")
                    .eq("shop_domain", tenant)
                    .in_("audit_id", normalized_audit_ids)
                    .order("created_at", desc=False)
                    .execute()
                )
                suggestions_res = (
                    client.table("product_intelligence_suggestions")
                    .select("*")
                    .eq("shop_domain", tenant)
                    .in_("audit_id", normalized_audit_ids)
                    .order("created_at", desc=False)
                    .execute()
                )

                audits_by_id = {
                    str(item.get("audit_id")): dict(item)
                    for item in (audits_res.data or [])
                    if item.get("audit_id")
                }
                findings_by_id: dict[str, list[dict[str, Any]]] = {}
                for item in findings_res.data or []:
                    audit_id = str(item.get("audit_id") or "").strip()
                    if not audit_id:
                        continue
                    findings_by_id.setdefault(audit_id, []).append(dict(item))
                suggestions_by_id: dict[str, list[dict[str, Any]]] = {}
                for item in suggestions_res.data or []:
                    audit_id = str(item.get("audit_id") or "").strip()
                    if not audit_id:
                        continue
                    suggestions_by_id.setdefault(audit_id, []).append(dict(item))

                artifacts: list[dict[str, Any]] = []
                for audit_id in normalized_audit_ids:
                    audit = audits_by_id.get(audit_id)
                    if not isinstance(audit, dict):
                        continue
                    artifacts.append(
                        {
                            "audit_id": audit_id,
                            "audit": {
                                **audit,
                                "findings": findings_by_id.get(audit_id, []),
                            },
                            "suggestions": suggestions_by_id.get(audit_id, []),
                        }
                    )
                return artifacts
            except Exception:
                LOG.exception("Failed listing batched product intelligence artifacts")

        artifacts = []
        for audit_id in normalized_audit_ids:
            audit = self.get_product_intelligence_audit(audit_id, shop_domain=tenant)
            if not audit:
                continue
            artifacts.append(
                {
                    "audit_id": audit_id,
                    "audit": audit,
                    "suggestions": self.list_product_intelligence_suggestions(
                        audit_id=audit_id,
                        shop_domain=tenant,
                    ),
                }
            )
        return artifacts

    def get_product_intelligence_audit(
        self,
        audit_id: str,
        *,
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None:
        tenant = str(shop_domain or "").strip().lower()
        if not tenant:
            return None
        client = self._get_supabase_client()
        if client:
            try:
                audit_res = (
                    client.table("product_intelligence_audits")
                    .select("*")
                    .eq("audit_id", audit_id)
                    .eq("shop_domain", tenant)
                    .limit(1)
                    .execute()
                )
                audit_rows = audit_res.data or []
                if not audit_rows:
                    return None
                findings_res = (
                    client.table("product_intelligence_findings")
                    .select("*")
                    .eq("audit_id", audit_id)
                    .eq("shop_domain", tenant)
                    .order("created_at", desc=False)
                    .execute()
                )
                audit = dict(audit_rows[0])
                audit["findings"] = findings_res.data or []
                return audit
            except Exception:
                LOG.exception("Failed fetching product intelligence audit %s", audit_id)

        audit = self.product_intelligence_audits.get(audit_id)
        if not audit:
            return None
        if str(audit.get("shop_domain") or "").strip().lower() != tenant:
            return None
        findings = [
            dict(item)
            for item in self.product_intelligence_findings.get(audit_id, [])
            if str(item.get("shop_domain") or tenant).strip().lower() == tenant
        ]
        return {**audit, "findings": findings}

    def save_product_intelligence_suggestions(
        self,
        *,
        audit_id: str,
        suggestions: list[dict[str, Any]],
        shop_domain: str | None = None,
    ) -> int:
        tenant = str(shop_domain or "").strip().lower()
        if not tenant:
            raise ValueError("Missing shop_domain for product intelligence suggestions")
        client = self._get_supabase_client()
        if client:
            try:
                client.table("product_intelligence_suggestions").delete().eq(
                    "audit_id", audit_id
                ).eq("shop_domain", tenant).execute()
                if suggestions:
                    payload = [
                        {**item, "audit_id": audit_id, "shop_domain": tenant}
                        for item in suggestions
                    ]
                    client.table("product_intelligence_suggestions").insert(
                        payload
                    ).execute()
                return len(suggestions)
            except Exception:
                LOG.exception(
                    "Failed saving intelligence suggestions for audit=%s", audit_id
                )
        for key, value in list(self.product_intelligence_suggestions.items()):
            if value.get("audit_id") != audit_id:
                continue
            if str(value.get("shop_domain") or "").strip().lower() == tenant:
                self.product_intelligence_suggestions.pop(key, None)
        for item in suggestions:
            suggestion_id = str(item.get("suggestion_id") or uuid.uuid4())
            self.product_intelligence_suggestions[suggestion_id] = {
                **item,
                "suggestion_id": suggestion_id,
                "audit_id": audit_id,
                "shop_domain": tenant,
            }
        return len(suggestions)

    def supersede_pending_product_intelligence_suggestions(
        self, *, product_ids: list[str], superseded_by_audit_id: str,
        shop_domain: str | None = None,
    ) -> int:
        tenant = str(shop_domain or "").strip().lower()
        ids = [str(value).strip() for value in product_ids if str(value).strip()]
        if not tenant or not ids:
            return 0
        now = self._utc_now()
        update = {
            "status": "superseded", "superseded_at": now,
            "superseded_by_audit_id": superseded_by_audit_id, "updated_at": now,
        }
        client = self._get_supabase_client()
        if client:
            try:
                rows = (client.table("product_intelligence_suggestions").update(update)
                        .eq("shop_domain", tenant).eq("status", "pending")
                        .in_("product_id", ids).neq("audit_id", superseded_by_audit_id)
                        .execute().data or [])
                return len(rows)
            except Exception:
                LOG.exception("Failed superseding pending intelligence suggestions")
        count = 0
        for item in self.product_intelligence_suggestions.values():
            if (str(item.get("shop_domain") or "").strip().lower() == tenant
                    and item.get("status") == "pending"
                    and str(item.get("product_id") or "") in ids
                    and item.get("audit_id") != superseded_by_audit_id):
                item.update(update)
                count += 1
        return count

    def list_product_intelligence_suggestions(
        self,
        *,
        audit_id: str,
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        tenant = str(shop_domain or "").strip().lower()
        if not tenant:
            return []
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("product_intelligence_suggestions")
                    .select("*")
                    .eq("audit_id", audit_id)
                    .eq("shop_domain", tenant)
                    .order("created_at", desc=False)
                    .execute()
                )
                return res.data or []
            except Exception:
                LOG.exception(
                    "Failed listing intelligence suggestions for audit=%s", audit_id
                )
        return [
            dict(item)
            for item in self.product_intelligence_suggestions.values()
            if item.get("audit_id") == audit_id
            and str(item.get("shop_domain") or "").strip().lower() == tenant
        ]

    def get_product_intelligence_suggestion(
        self,
        suggestion_id: str,
        *,
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None:
        tenant = str(shop_domain or "").strip().lower()
        if not tenant:
            return None
        cached_item = self.product_intelligence_suggestions.get(suggestion_id)
        if (
            cached_item
            and str(cached_item.get("shop_domain") or "").strip().lower() != tenant
        ):
            cached_item = None
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("product_intelligence_suggestions")
                    .select("*")
                    .eq("suggestion_id", suggestion_id)
                    .eq("shop_domain", tenant)
                    .limit(1)
                    .execute()
                )
                rows = res.data or []
                if rows:
                    db_item = rows[0]
                    if cached_item:
                        return {**db_item, **cached_item}
                    return db_item
            except Exception:
                LOG.exception(
                    "Failed fetching intelligence suggestion %s", suggestion_id
                )
        return cached_item

    def create_product_intelligence_suggestion(
        self,
        *,
        suggestion: dict[str, Any],
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(suggestion, dict):
            return None
        tenant = str(shop_domain or suggestion.get("shop_domain") or "").strip().lower()
        if not tenant:
            raise ValueError("Missing shop_domain for product intelligence suggestion")
        suggestion_id = str(suggestion.get("suggestion_id") or uuid.uuid4())
        payload = {**suggestion, "suggestion_id": suggestion_id, "shop_domain": tenant}
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("product_intelligence_suggestions")
                    .insert(payload)
                    .execute()
                )
                rows = res.data or []
                if rows:
                    created = dict(rows[0])
                    self.product_intelligence_suggestions[suggestion_id] = created
                    return created
            except Exception:
                LOG.exception(
                    "Failed creating intelligence suggestion %s", suggestion_id
                )
        self.product_intelligence_suggestions[suggestion_id] = dict(payload)
        return dict(payload)

    def mark_product_intelligence_suggestion_applied(
        self,
        *,
        suggestion_id: str,
        previous_payload: dict[str, Any] | None = None,
        patch_payload: dict[str, Any] | None = None,
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None:
        tenant = str(shop_domain or "").strip().lower()
        if not tenant:
            return None
        now = self._utc_now()
        update_payload: dict[str, Any] = {
            "status": "applied",
            "applied_at": now,
            "updated_at": now,
        }
        if isinstance(previous_payload, dict):
            update_payload["previous_payload"] = previous_payload
        if isinstance(patch_payload, dict):
            update_payload["patch_payload"] = patch_payload
        client = self._get_supabase_client()
        if client:
            try:

                def _execute_update(payload: dict[str, Any]) -> list[dict[str, Any]]:
                    response = (
                        client.table("product_intelligence_suggestions")
                        .update(payload)
                        .eq("suggestion_id", suggestion_id)
                        .eq("shop_domain", tenant)
                        .execute()
                    )
                    return response.data or []

                rows = _execute_update(update_payload)
                if rows:
                    cached = dict(rows[0])
                    if isinstance(previous_payload, dict):
                        cached["previous_payload"] = previous_payload
                    self.product_intelligence_suggestions[suggestion_id] = cached
                    return cached
                return None
            except Exception:
                if "previous_payload" in update_payload:
                    fallback_payload = dict(update_payload)
                    fallback_payload.pop("previous_payload", None)
                    try:
                        rows = (
                            client.table("product_intelligence_suggestions")
                            .update(fallback_payload)
                            .eq("suggestion_id", suggestion_id)
                            .eq("shop_domain", tenant)
                            .execute()
                            .data
                            or []
                        )
                        if rows:
                            cached = dict(rows[0])
                            if isinstance(previous_payload, dict):
                                cached["previous_payload"] = previous_payload
                            self.product_intelligence_suggestions[suggestion_id] = (
                                cached
                            )
                            return cached
                        return None
                    except Exception:
                        LOG.exception(
                            "Failed marking intelligence suggestion applied %s",
                            suggestion_id,
                        )
                else:
                    LOG.exception(
                        "Failed marking intelligence suggestion applied %s",
                        suggestion_id,
                    )
        item = self.product_intelligence_suggestions.get(suggestion_id)
        if not item:
            return None
        if str(item.get("shop_domain") or "").strip().lower() != tenant:
            return None
        item["status"] = "applied"
        item["applied_at"] = now
        item["updated_at"] = now
        if isinstance(previous_payload, dict):
            item["previous_payload"] = previous_payload
        if isinstance(patch_payload, dict):
            item["patch_payload"] = patch_payload
        return dict(item)

    def mark_product_intelligence_suggestion_pending(
        self,
        *,
        suggestion_id: str,
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None:
        tenant = str(shop_domain or "").strip().lower()
        if not tenant:
            return None
        now = self._utc_now()
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("product_intelligence_suggestions")
                    .update(
                        {
                            "status": "pending",
                            "applied_at": None,
                            "updated_at": now,
                        }
                    )
                    .eq("suggestion_id", suggestion_id)
                    .eq("shop_domain", tenant)
                    .execute()
                )
                rows = res.data or []
                if rows:
                    cached = dict(rows[0])
                    self.product_intelligence_suggestions[suggestion_id] = cached
                    return cached
                return None
            except Exception:
                LOG.exception(
                    "Failed marking intelligence suggestion pending %s", suggestion_id
                )
        item = self.product_intelligence_suggestions.get(suggestion_id)
        if not item:
            return None
        if str(item.get("shop_domain") or "").strip().lower() != tenant:
            return None
        item["status"] = "pending"
        item["applied_at"] = None
        item["updated_at"] = now
        cached = dict(item)
        self.product_intelligence_suggestions[suggestion_id] = cached
        return cached

    def mark_product_intelligence_suggestion_reverted(
        self,
        *,
        suggestion_id: str,
        shop_domain: str | None = None,
    ) -> dict[str, Any] | None:
        tenant = str(shop_domain or "").strip().lower()
        if not tenant:
            return None
        now = self._utc_now()
        update_payload = {"status": "reverted", "reverted_at": now, "updated_at": now}
        client = self._get_supabase_client()
        if client:
            try:
                rows = (
                    client.table("product_intelligence_suggestions")
                    .update(update_payload)
                    .eq("suggestion_id", suggestion_id)
                    .eq("shop_domain", tenant)
                    .eq("status", "applied")
                    .execute().data or []
                )
                if rows:
                    cached = dict(rows[0])
                    self.product_intelligence_suggestions[suggestion_id] = cached
                    return cached
                return None
            except Exception:
                LOG.exception("Failed marking intelligence suggestion reverted %s", suggestion_id)
        item = self.product_intelligence_suggestions.get(suggestion_id)
        if not item or str(item.get("shop_domain") or "").strip().lower() != tenant:
            return None
        if str(item.get("status") or "") != "applied":
            return None
        item.update(update_payload)
        return dict(item)

    def mark_product_intelligence_suggestion_superseded(
        self, *, suggestion_id: str, shop_domain: str | None = None
    ) -> dict[str, Any] | None:
        tenant = str(shop_domain or "").strip().lower()
        if not tenant:
            return None
        now = self._utc_now()
        update = {"status": "superseded", "superseded_at": now, "updated_at": now}
        client = self._get_supabase_client()
        if client:
            try:
                rows = (client.table("product_intelligence_suggestions").update(update)
                        .eq("suggestion_id", suggestion_id).eq("shop_domain", tenant)
                        .eq("status", "pending").execute().data or [])
                if rows:
                    self.product_intelligence_suggestions[suggestion_id] = dict(rows[0])
                    return dict(rows[0])
                return None
            except Exception:
                LOG.exception("Failed superseding intelligence suggestion %s", suggestion_id)
        item = self.product_intelligence_suggestions.get(suggestion_id)
        if not item or str(item.get("shop_domain") or "").strip().lower() != tenant:
            return None
        if item.get("status") != "pending":
            return None
        item.update(update)
        return dict(item)

    def get_product_intelligence_bulk_operation(
        self,
        *,
        operation_type: str,
        idempotency_key: str,
        shop_domain: str,
    ) -> dict[str, Any] | None:
        tenant = str(shop_domain or "").strip().lower()
        normalized_operation = str(operation_type or "").strip().lower()
        normalized_key = str(idempotency_key or "").strip()
        if not tenant or not normalized_operation or not normalized_key:
            return None
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("product_intelligence_bulk_operations")
                    .select("*")
                    .eq("shop_domain", tenant)
                    .eq("operation_type", normalized_operation)
                    .eq("idempotency_key", normalized_key)
                    .limit(1)
                    .execute()
                )
                rows = res.data or []
                if rows:
                    row = dict(rows[0])
                    self._bulk_operation_store()[
                        f"{tenant}:{normalized_operation}:{normalized_key}"
                    ] = row
                    return row
            except Exception:
                LOG.exception(
                    "Failed fetching intelligence bulk operation for shop=%s operation=%s",
                    tenant,
                    normalized_operation,
                )
        cached = self._bulk_operation_store().get(
            f"{tenant}:{normalized_operation}:{normalized_key}"
        )
        if isinstance(cached, dict):
            return dict(cached)
        return None

    def upsert_product_intelligence_bulk_operation(
        self,
        *,
        operation_type: str,
        idempotency_key: str,
        request_hash: str,
        response: dict[str, Any],
        shop_domain: str,
        status: str = "succeeded",
    ) -> dict[str, Any]:
        tenant = str(shop_domain or "").strip().lower()
        normalized_operation = str(operation_type or "").strip().lower()
        normalized_key = str(idempotency_key or "").strip()
        normalized_hash = str(request_hash or "").strip()
        normalized_status = str(status or "").strip().lower() or "succeeded"
        if (
            not tenant
            or not normalized_operation
            or not normalized_key
            or not normalized_hash
        ):
            raise ValueError("Invalid bulk operation idempotency payload")
        if not isinstance(response, dict):
            raise ValueError("response must be an object")
        now = self._utc_now()
        payload = {
            "shop_domain": tenant,
            "operation_type": normalized_operation,
            "idempotency_key": normalized_key,
            "request_hash": normalized_hash,
            "status": normalized_status,
            "response": response,
            "created_at": now,
            "updated_at": now,
        }
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("product_intelligence_bulk_operations")
                    .upsert(
                        payload,
                        on_conflict="shop_domain,operation_type,idempotency_key",
                    )
                    .execute()
                )
                rows = res.data or []
                if rows:
                    row = dict(rows[0])
                    self._bulk_operation_store()[
                        f"{tenant}:{normalized_operation}:{normalized_key}"
                    ] = row
                    return row
            except Exception:
                LOG.exception(
                    "Failed upserting intelligence bulk operation for shop=%s operation=%s",
                    tenant,
                    normalized_operation,
                )
        self._bulk_operation_store()[
            f"{tenant}:{normalized_operation}:{normalized_key}"
        ] = dict(payload)
        return dict(payload)

    @staticmethod
    def _default_product_intelligence_normalization_settings() -> dict[str, Any]:
        return {
            "unit_system": "metric",
            "locale_default_unit_system": None,
            "confidence_threshold": None,
            "categories": {key: True for key in NORMALIZATION_CATEGORY_KEYS},
        }

    @staticmethod
    def _coerce_product_intelligence_normalization_settings(
        settings: dict[str, Any],
        *,
        fallback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base = {
            **SupabaseIntelligenceMixin._default_product_intelligence_normalization_settings(),
            **(fallback or {}),
        }
        raw_unit = (
            str(settings.get("unit_system") or base.get("unit_system") or "metric")
            .strip()
            .lower()
        )
        unit_system = raw_unit if raw_unit in {"metric", "imperial"} else "metric"
        raw_locale_default = settings.get(
            "locale_default_unit_system", base.get("locale_default_unit_system")
        )
        locale_default = (
            str(raw_locale_default).strip().lower()
            if isinstance(raw_locale_default, str)
            else None
        )
        if locale_default not in {"metric", "imperial"}:
            locale_default = None

        raw_confidence = settings.get(
            "confidence_threshold", base.get("confidence_threshold")
        )
        if raw_confidence in (None, ""):
            confidence_threshold = None
        elif isinstance(raw_confidence, (int, float)):
            confidence_threshold = max(0.0, min(1.0, float(raw_confidence)))
        else:
            raise ValueError("Invalid confidence_threshold")

        raw_categories = settings.get("categories")
        base_categories = (
            base.get("categories") if isinstance(base.get("categories"), dict) else {}
        )
        categories_input = raw_categories if isinstance(raw_categories, dict) else {}
        categories = {
            key: (
                categories_input[key]
                if key in categories_input and isinstance(categories_input[key], bool)
                else bool(base_categories.get(key, True))
            )
            for key in NORMALIZATION_CATEGORY_KEYS
        }

        return {
            "unit_system": unit_system,
            "locale_default_unit_system": locale_default,
            "confidence_threshold": confidence_threshold,
            "categories": categories,
        }

    def get_product_intelligence_normalization_settings(
        self,
        *,
        shop_domain: str,
    ) -> dict[str, Any] | None:
        tenant = str(shop_domain or "").strip().lower()
        if not tenant:
            return None

        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("product_intelligence_normalization_settings")
                    .select("*")
                    .eq("shop_domain", tenant)
                    .limit(1)
                    .execute()
                )
                rows = res.data or []
                if rows:
                    row = dict(rows[0])
                    out = {
                        "shop_domain": tenant,
                        "unit_system": row.get("unit_system"),
                        "locale_default_unit_system": row.get(
                            "locale_default_unit_system"
                        ),
                        "confidence_threshold": row.get("confidence_threshold"),
                        "categories": (
                            row.get("categories")
                            if isinstance(row.get("categories"), dict)
                            else {}
                        ),
                        "updated_at": row.get("updated_at"),
                    }
                    out = {
                        **out,
                        **self._coerce_product_intelligence_normalization_settings(
                            out, fallback=out
                        ),
                    }
                    self.product_intelligence_normalization_settings[tenant] = dict(out)
                    return out
            except Exception:
                LOG.exception(
                    "Failed fetching product intelligence normalization settings for shop=%s",
                    tenant,
                )

        cached = self.product_intelligence_normalization_settings.get(tenant)
        if cached:
            return dict(cached)
        return None

    def upsert_product_intelligence_normalization_settings(
        self,
        *,
        shop_domain: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        tenant = str(shop_domain or "").strip().lower()
        if not tenant:
            raise ValueError("Missing shop_domain for normalization settings")

        existing = self.get_product_intelligence_normalization_settings(
            shop_domain=tenant
        ) or {
            **self._default_product_intelligence_normalization_settings(),
            "shop_domain": tenant,
        }
        normalized = self._coerce_product_intelligence_normalization_settings(
            settings,
            fallback=existing,
        )
        now = self._utc_now()
        payload = {
            "shop_domain": tenant,
            "unit_system": normalized["unit_system"],
            "locale_default_unit_system": normalized["locale_default_unit_system"],
            "confidence_threshold": normalized["confidence_threshold"],
            "categories": normalized["categories"],
            "updated_at": now,
        }

        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("product_intelligence_normalization_settings")
                    .upsert(payload, on_conflict="shop_domain")
                    .execute()
                )
                rows = res.data or []
                if rows:
                    row = dict(rows[0])
                    out = {
                        "shop_domain": tenant,
                        "unit_system": row.get(
                            "unit_system", normalized["unit_system"]
                        ),
                        "locale_default_unit_system": row.get(
                            "locale_default_unit_system"
                        ),
                        "confidence_threshold": row.get("confidence_threshold"),
                        "categories": (
                            row.get("categories")
                            if isinstance(row.get("categories"), dict)
                            else normalized["categories"]
                        ),
                        "updated_at": row.get("updated_at"),
                    }
                    out = {
                        **out,
                        **self._coerce_product_intelligence_normalization_settings(
                            out, fallback=out
                        ),
                    }
                    self.product_intelligence_normalization_settings[tenant] = dict(out)
                    return out
            except Exception:
                LOG.exception(
                    "Failed upserting product intelligence normalization settings for shop=%s",
                    tenant,
                )

        fallback_out = {
            "shop_domain": tenant,
            **normalized,
            "updated_at": now,
        }
        self.product_intelligence_normalization_settings[tenant] = dict(fallback_out)
        return fallback_out
