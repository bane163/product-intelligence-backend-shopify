import logging
import os
import uuid
from base64 import urlsafe_b64encode
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from cryptography.fernet import Fernet

from objects.sanitize import sanitize_text
from .interfaces import SupabaseServiceInterface

LOG = logging.getLogger(__name__)

NORMALIZATION_CATEGORY_KEYS = (
    "size_alias",
    "mixed_units",
    "structured_unstructured_size",
    "dimensions_format",
    "supplier_size",
    "variant_ordering",
    "description_extraction",
    "children_size",
    "missing_options",
)

FILE_ORIGIN_MERCHANT_UPLOAD = "merchant_upload"
FILE_ORIGIN_WORKFLOW_OUTPUT = "workflow_output"
FILE_ORIGIN_SOURCE_HIGHLIGHT = "source_highlight"
FILE_ORIGIN_DRAFT_RESUME = "draft_resume"
FILE_ORIGIN_SUBMITTED_RESUME = "submitted_resume"


class SupabaseService(SupabaseServiceInterface):
    def __init__(self, bucket_name: str | None = None):
        self.bucket_name = bucket_name or os.environ.get(
            "FILES_BUCKET_NAME", "documents"
        )
        self.file_storage: dict[str, dict[str, Any]] = {}
        self.product_drafts: dict[str, dict[str, Any]] = {}
        self.submitted_documents: dict[str, dict[str, Any]] = {}
        self.product_intelligence_audits: dict[str, dict[str, Any]] = {}
        self.product_intelligence_findings: dict[str, list[dict[str, Any]]] = {}
        self.product_intelligence_suggestions: dict[str, dict[str, Any]] = {}
        self.product_intelligence_normalization_settings: dict[str, dict[str, Any]] = {}
        self.llm_model_configs: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _thumbnail_path(file_id: str) -> str:
        return f"thumbnails/{file_id}.png"

    def _try_get_bucket(self):
        try:
            from supabase_client import get_storage
        except Exception:
            LOG.exception("Failed to import Supabase storage helper")
            return None

        try:
            storage = get_storage()
            bucket = storage.from_(self.bucket_name)
            return bucket
        except Exception:
            LOG.exception("Supabase storage unavailable; using in-memory storage")
            return None

    def _get_supabase_client(self):
        try:
            from supabase_client import get_supabase

            return get_supabase()
        except Exception:
            LOG.debug("Supabase client unavailable", exc_info=True)
            return None

    @staticmethod
    def _normalize_file_origin(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        return normalized or None

    @classmethod
    def _infer_file_origin_from_filename(cls, filename: Any) -> str:
        name = str(filename or "").strip().lower()
        if not name:
            return FILE_ORIGIN_MERCHANT_UPLOAD
        if "-source-highlight" in name:
            return FILE_ORIGIN_SOURCE_HIGHLIGHT
        if name.startswith("draft-") and name.endswith(".xlsx"):
            return FILE_ORIGIN_DRAFT_RESUME
        if name.startswith("submitted-") and name.endswith(".xlsx"):
            return FILE_ORIGIN_SUBMITTED_RESUME
        if name.endswith("-products.xlsx"):
            return FILE_ORIGIN_WORKFLOW_OUTPUT
        return FILE_ORIGIN_MERCHANT_UPLOAD

    @classmethod
    def _normalize_file_row_origin(cls, row: dict[str, Any]) -> str:
        return (
            cls._normalize_file_origin(row.get("file_origin"))
            or cls._infer_file_origin_from_filename(row.get("filename"))
        )

    @classmethod
    def _dedupe_and_filter_document_rows(
        cls, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        deduped: dict[str, dict[str, Any]] = {}
        for item in rows:
            storage_path = str(item.get("storage_path") or item.get("file_id") or "").strip()
            if not storage_path:
                continue
            current = deduped.get(storage_path)
            if current is None:
                deduped[storage_path] = item
                continue
            current_created = str(current.get("created_at") or "")
            candidate_created = str(item.get("created_at") or "")
            if candidate_created >= current_created:
                deduped[storage_path] = item

        visible_rows: list[dict[str, Any]] = []
        for item in deduped.values():
            if cls._normalize_file_row_origin(item) != FILE_ORIGIN_MERCHANT_UPLOAD:
                continue
            visible_rows.append(
                {
                    **item,
                    "file_id": str(item.get("storage_path") or item.get("file_id") or ""),
                }
            )
        visible_rows.sort(
            key=lambda item: str(item.get("created_at") or ""),
            reverse=True,
        )
        return visible_rows

    # ----- File storage -----
    def save_file(
        self,
        file_id: str,
        name: str,
        content: bytes,
        content_type: str | None = None,
        file_origin: str | None = None,
    ) -> None:
        normalized_origin = self._normalize_file_origin(file_origin) or self._infer_file_origin_from_filename(name)
        bucket = self._try_get_bucket()
        if bucket is None:
            self.file_storage[file_id] = {
                "name": name,
                "content": content,
                "content_type": content_type or "application/octet-stream",
                "storage_path": file_id,
                "file_origin": normalized_origin,
                "thumbnail_storage_path": None,
                "thumbnail_content": None,
            }
            return

        safe_content_type = content_type or "application/octet-stream"
        if safe_content_type.startswith("text/"):
            safe_content_type = "application/octet-stream"

        try:
            bucket.upload(
                file_id,
                content,
                {
                    "content-type": safe_content_type,
                    "metadata": {"name": name},
                },
            )
        except Exception:
            try:
                bucket.update(
                    file_id,
                    content,
                    {
                        "content-type": safe_content_type,
                        "metadata": {"name": name},
                    },
                )
            except Exception:
                bucket.upload(
                    file_id,
                    content,
                    {"content-type": safe_content_type, "upsert": "true"},
                )

        try:
            client = self._get_supabase_client()
            if client:
                metadata_payload = {
                    "storage_path": file_id,
                    "filename": name,
                    "content_type": content_type or "application/octet-stream",
                    "size": len(content) if content else 0,
                    "file_origin": normalized_origin,
                }
                try:
                    client.table("file_metadata").upsert(
                        metadata_payload, on_conflict="storage_path"
                    ).execute()
                except Exception:
                    legacy_payload = dict(metadata_payload)
                    legacy_payload.pop("file_origin", None)
                    try:
                        client.table("file_metadata").upsert(
                            legacy_payload, on_conflict="storage_path"
                        ).execute()
                    except Exception:
                        client.table("file_metadata").insert(legacy_payload).execute()
        except Exception:
            LOG.exception("Failed inserting file metadata for %s", file_id)

    def list_files(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        try:
            client = self._get_supabase_client()
            if client:
                db_limit = min(max(limit + offset, 1000), 5000)
                res = (
                    client.table("file_metadata")
                    .select("*")
                    .order("created_at", desc=True)
                    .limit(db_limit)
                    .execute()
                )
                rows = self._dedupe_and_filter_document_rows(res.data or [])
                return rows[offset : offset + limit]
        except Exception:
            LOG.exception("Failed listing files from DB")

        bucket = self._try_get_bucket()
        if bucket is None:
            rows = [
                {
                    "file_id": k,
                    "storage_path": k,
                    "filename": v["name"],
                    "content_type": v["content_type"],
                    "file_origin": v.get("file_origin"),
                    "created_at": v.get("created_at") or self._utc_now(),
                    "thumbnail_storage_path": v.get("thumbnail_storage_path"),
                }
                for k, v in self.file_storage.items()
            ]
            filtered_rows = self._dedupe_and_filter_document_rows(rows)
            return filtered_rows[offset : offset + limit]

        try:
            files = bucket.list(path=None)
            rows = [
                {
                    "file_id": f.get("name"),
                    "storage_path": f.get("name"),
                    "filename": f.get("metadata", {}).get("name", f.get("name")),
                    "content_type": f.get("metadata", {}).get("mimetype"),
                    "file_origin": self._infer_file_origin_from_filename(
                        f.get("metadata", {}).get("name", f.get("name"))
                    ),
                    "size": f.get("metadata", {}).get("size"),
                    "created_at": f.get("created_at"),
                    "thumbnail_storage_path": None,
                }
                for f in files
            ]
            filtered_rows = self._dedupe_and_filter_document_rows(rows)
            return filtered_rows[offset : offset + limit]
        except Exception:
            LOG.exception("Failed listing files from bucket")
            return []

    def get_file(self, file_id: str) -> dict[str, Any] | None:
        bucket = self._try_get_bucket()
        if bucket is None:
            return self.file_storage.get(file_id)

        try:
            data = bucket.download(file_id)
        except Exception:
            return None

        try:
            content = data.read() if hasattr(data, "read") else data
        except Exception:
            LOG.exception("Failed to read downloaded data for %s", file_id)
            return None

        name = file_id
        content_type = "application/octet-stream"
        file_origin = FILE_ORIGIN_MERCHANT_UPLOAD
        thumbnail_storage_path = None
        try:
            client = self._get_supabase_client()
            if client:
                res = (
                    client.table("file_metadata")
                    .select("*")
                    .eq("storage_path", file_id)
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                rows = res.data or []
                if rows:
                    meta = rows[0]
                    name = meta.get("filename", name)
                    content_type = meta.get("content_type", content_type)
                    file_origin = self._normalize_file_row_origin(meta)
                    thumbnail_storage_path = meta.get("thumbnail_storage_path")
        except Exception:
            LOG.debug("DB metadata fetch failed for %s", file_id, exc_info=True)

        return {
            "name": name,
            "content": content,
            "content_type": content_type,
            "file_origin": file_origin,
            "storage_path": file_id,
            "thumbnail_storage_path": thumbnail_storage_path,
        }

    def save_file_thumbnail(self, *, file_id: str, content: bytes) -> str | None:
        bucket = self._try_get_bucket()
        thumbnail_path = self._thumbnail_path(file_id)
        if bucket is None:
            file_entry = self.file_storage.get(file_id)
            if not file_entry:
                return None
            file_entry["thumbnail_content"] = content
            file_entry["thumbnail_storage_path"] = thumbnail_path
            return thumbnail_path

        try:
            bucket.upload(
                thumbnail_path,
                content,
                {"content-type": "image/png", "upsert": "true"},
            )
        except Exception:
            try:
                bucket.update(
                    thumbnail_path,
                    content,
                    {"content-type": "image/png"},
                )
            except Exception:
                LOG.exception("Failed saving thumbnail for file %s", file_id)
                return None

        try:
            client = self._get_supabase_client()
            if client:
                existing = (
                    client.table("file_metadata")
                    .select("storage_path")
                    .eq("storage_path", file_id)
                    .limit(1)
                    .execute()
                )
                rows = existing.data or []
                if rows:
                    client.table("file_metadata").update(
                        {"thumbnail_storage_path": thumbnail_path}
                    ).eq("storage_path", file_id).execute()
                else:
                    file_entry = self.get_file(file_id)
                    if not file_entry:
                        LOG.error(
                            "Cannot persist thumbnail path: missing file entry for %s",
                            file_id,
                        )
                        return None
                    metadata_payload = {
                        "storage_path": file_id,
                        "filename": file_entry.get("name") or file_id,
                        "content_type": file_entry.get("content_type")
                        or "application/octet-stream",
                        "size": len(file_entry.get("content") or b""),
                        "thumbnail_storage_path": thumbnail_path,
                    }
                    metadata_payload_with_origin = {
                        **metadata_payload,
                        "file_origin": self._normalize_file_origin(
                            file_entry.get("file_origin")
                        )
                        or self._infer_file_origin_from_filename(
                            file_entry.get("name") or file_id
                        ),
                    }
                    try:
                        client.table("file_metadata").insert(
                            metadata_payload_with_origin
                        ).execute()
                    except Exception:
                        client.table("file_metadata").insert(metadata_payload).execute()
        except Exception:
            LOG.exception(
                "Failed updating thumbnail metadata for %s; verify thumbnail_storage_path migration is applied",
                file_id,
            )
            return None
        return thumbnail_path

    def get_file_thumbnail(self, file_id: str) -> bytes | None:
        file_entry = self.get_file(file_id)
        if not file_entry:
            return None
        thumbnail_path = file_entry.get("thumbnail_storage_path")
        if not thumbnail_path:
            return None

        bucket = self._try_get_bucket()
        if bucket is None:
            return file_entry.get("thumbnail_content")

        try:
            data = bucket.download(thumbnail_path)
            return data.read() if hasattr(data, "read") else data
        except Exception:
            LOG.debug(
                "Failed downloading thumbnail %s for %s",
                thumbnail_path,
                file_id,
                exc_info=True,
            )
            return None

    def delete_file(self, file_id: str) -> bool:
        bucket = self._try_get_bucket()
        if bucket is None:
            if file_id in self.file_storage:
                del self.file_storage[file_id]
                return True
            return False

        thumbnail_path = None
        try:
            client = self._get_supabase_client()
            if client:
                existing = (
                    client.table("file_metadata")
                    .select("thumbnail_storage_path")
                    .eq("storage_path", file_id)
                    .limit(1)
                    .execute()
                )
                existing_rows = existing.data or []
                if existing_rows:
                    thumbnail_path = existing_rows[0].get("thumbnail_storage_path")
                client.table("file_metadata").delete().eq(
                    "storage_path", file_id
                ).execute()
        except Exception:
            LOG.warning("Failed deleting metadata for %s", file_id, exc_info=True)

        if thumbnail_path:
            try:
                bucket.remove([thumbnail_path])
            except Exception:
                LOG.warning(
                    "Failed deleting thumbnail object %s for %s",
                    thumbnail_path,
                    file_id,
                    exc_info=True,
                )

        try:
            bucket.remove([file_id])
            return True
        except Exception:
            return False

    # ----- Run logging -----
    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_shop_domain(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        return normalized or None

    @staticmethod
    def _normalize_run_status(value: Any) -> str | None:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return None
        if normalized in {"queued", "pending", "created"}:
            return "queued"
        if normalized in {"running", "in_progress", "processing"}:
            return "running"
        if normalized in {"succeeded", "success", "completed", "complete", "done"}:
            return "succeeded"
        if normalized in {"failed", "error", "errored"}:
            return "failed"
        if normalized in {"cancelled", "canceled", "aborted"}:
            return "cancelled"
        return normalized

    def create_or_update_run(self, run_id: str, fields: dict[str, Any]) -> None:
        client = self._get_supabase_client()
        if not client:
            return
        payload = {"run_id": run_id, **fields}
        if "shop_domain" in payload:
            payload["shop_domain"] = self._normalize_shop_domain(payload.get("shop_domain"))
        if "status" in payload:
            payload["status"] = self._normalize_run_status(payload.get("status"))
        for key in ("prompt", "writer_prompt", "error"):
            if key in payload:
                payload[key] = sanitize_text(payload.get(key))
        try:
            client.table("llm_runs").upsert(payload, on_conflict="run_id").execute()
        except Exception:
            LOG.exception("Failed upserting llm_runs row for run_id=%s", run_id)

    def append_run_event(self, run_id: str, event: dict[str, Any], seq: int) -> None:
        client = self._get_supabase_client()
        if not client:
            return
        try:
            client.table("llm_run_events").insert(
                {
                    "run_id": run_id,
                    "ts": event.get("ts") or self._utc_now(),
                    "phase": event.get("phase", "unknown"),
                    "level": event.get("level", "info"),
                    "message": sanitize_text(event.get("message")) or "",
                    "payload_preview": sanitize_text(event.get("payload_preview")),
                    "error": sanitize_text(event.get("error")),
                    "seq": seq,
                }
            ).execute()
        except Exception:
            LOG.exception("Failed inserting llm_run_events row for run_id=%s", run_id)

    def append_run_message(
        self,
        run_id: str,
        *,
        role: str,
        message: Any,
        seq: int,
        meta: dict[str, Any] | None = None,
    ) -> None:
        client = self._get_supabase_client()
        if not client:
            return
        body = sanitize_text(message)
        if not body:
            return
        try:
            client.table("llm_run_messages").insert(
                {
                    "run_id": run_id,
                    "role": role,
                    "message": body,
                    "meta": meta or {},
                    "seq": seq,
                }
            ).execute()
        except Exception:
            LOG.exception("Failed inserting llm_run_messages row for run_id=%s", run_id)

    def finalize_run(
        self,
        run_id: str,
        *,
        status: str,
        duration_ms: int | None = None,
        error: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        normalized_status = self._normalize_run_status(status) or "failed"
        fields: dict[str, Any] = {
            "status": normalized_status,
            "ended_at": self._utc_now(),
            "duration_ms": duration_ms,
        }
        if error:
            fields["error"] = error
        if normalized_status == "failed":
            fields.setdefault("failure_code", "run_failed")
            fields.setdefault("failure_message", sanitize_text(error) if error else "Run failed")
            fields.setdefault("resume_token", str(uuid.uuid4()))
        elif normalized_status in {"succeeded", "cancelled"}:
            fields.setdefault("resume_token", None)
            fields.setdefault("failure_code", None)
            fields.setdefault("failure_message", None)
        if extra_fields:
            fields.update(extra_fields)
        self.create_or_update_run(run_id, fields)

    def list_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        client = self._get_supabase_client()
        if not client:
            return []
        try:
            query = (
                client.table("llm_runs")
                .select("*")
                .order("created_at", desc=True)
                .range(offset, offset + limit - 1)
            )
            if status:
                query = query.eq("status", status)
            normalized_shop_domain = self._normalize_shop_domain(shop_domain)
            if normalized_shop_domain:
                query = query.eq("shop_domain", normalized_shop_domain)
            res = query.execute()
            return res.data or []
        except Exception:
            LOG.exception("Failed listing llm_runs")
            return []

    def get_run(self, run_id: str, *, shop_domain: str | None = None) -> dict[str, Any] | None:
        client = self._get_supabase_client()
        if not client:
            return None
        try:
            query = client.table("llm_runs").select("*").eq("run_id", run_id)
            normalized_shop_domain = self._normalize_shop_domain(shop_domain)
            if normalized_shop_domain:
                query = query.eq("shop_domain", normalized_shop_domain)
            res = query.limit(1).execute()
            rows = res.data or []
            return rows[0] if rows else None
        except Exception:
            LOG.exception("Failed fetching llm_runs for run_id=%s", run_id)
            return None

    def get_run_history(self, run_id: str, *, shop_domain: str | None = None) -> dict[str, Any]:
        client = self._get_supabase_client()
        if not client:
            return {"run": None, "events": [], "messages": []}
        run = self.get_run(run_id, shop_domain=shop_domain)
        if not run:
            return {"run": None, "events": [], "messages": []}
        events: list[dict[str, Any]] = []
        messages: list[dict[str, Any]] = []
        try:
            res_events = (
                client.table("llm_run_events")
                .select("*")
                .eq("run_id", run_id)
                .order("seq")
                .limit(1000)
                .execute()
            )
            events = res_events.data or []
        except Exception:
            LOG.exception("Failed fetching llm_run_events for run_id=%s", run_id)

        try:
            res_messages = (
                client.table("llm_run_messages")
                .select("*")
                .eq("run_id", run_id)
                .order("seq")
                .limit(1000)
                .execute()
            )
            messages = res_messages.data or []
        except Exception:
            LOG.exception("Failed fetching llm_run_messages for run_id=%s", run_id)

        return {"run": run, "events": events, "messages": messages}

    def save_product_draft(
        self,
        *,
        draft_id: str,
        run_id: str | None,
        import_mode: str,
        draft_name: str | None,
        input_file_id: str | None = None,
        input_filename: str | None = None,
        output_file_id: str | None = None,
        output_filename: str | None = None,
        products: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = self._utc_now()
        first_title = ""
        if products and isinstance(products[0], dict):
            first_title = str(products[0].get("title") or "")
        payload = {
            "draft_id": draft_id,
            "run_id": run_id,
            "import_mode": import_mode,
            "draft_name": draft_name,
            "input_file_id": input_file_id,
            "input_filename": input_filename,
            "output_file_id": output_file_id,
            "output_filename": output_filename,
            "products": products,
            "product_count": len(products),
            "first_product_title": first_title,
            "created_at": now,
            "updated_at": now,
        }
        client = self._get_supabase_client()
        if client:
            try:
                client.table("product_drafts").upsert(
                    payload, on_conflict="draft_id"
                ).execute()
                return payload
            except Exception:
                LOG.exception("Failed saving product draft %s", draft_id)
                try:
                    compat_payload = dict(payload)
                    compat_payload.pop("first_product_title", None)
                    compat_payload.pop("draft_name", None)
                    compat_payload.pop("input_file_id", None)
                    compat_payload.pop("input_filename", None)
                    compat_payload.pop("output_file_id", None)
                    compat_payload.pop("output_filename", None)
                    client.table("product_drafts").upsert(
                        compat_payload, on_conflict="draft_id"
                    ).execute()
                except Exception:
                    LOG.exception(
                        "Fallback save for product draft %s also failed", draft_id
                    )
        self.product_drafts[draft_id] = payload
        return payload

    def list_product_drafts(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        sort_by: str = "date",
        sort_dir: str = "desc",
    ) -> list[dict[str, Any]]:
        db_drafts: list[dict[str, Any]] = []
        submitted_draft_ids: set[str] = set()
        db_drafts_loaded = False
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("product_drafts")
                    .select("*")
                    .order("created_at", desc=True)
                    .limit(1000)
                    .execute()
                )
                db_drafts = res.data or []
                db_drafts_loaded = True
            except Exception:
                LOG.exception("Failed listing product drafts")
            try:
                submitted_res = (
                    client.table("submitted_documents")
                    .select("draft_id")
                    .limit(1000)
                    .execute()
                )
                for item in submitted_res.data or []:
                    draft_id = item.get("draft_id")
                    if draft_id:
                        submitted_draft_ids.add(str(draft_id))
            except Exception:
                LOG.debug(
                    "Submitted documents table unavailable for draft filtering",
                    exc_info=True,
                )

        if db_drafts_loaded:
            drafts_map: dict[str, dict[str, Any]] = {
                str(item.get("draft_id")): item
                for item in db_drafts
                if item.get("draft_id")
            }
        else:
            drafts_map = {
                str(item.get("draft_id") or key): item
                for key, item in self.product_drafts.items()
            }
            for item in self.submitted_documents.values():
                draft_id = item.get("draft_id")
                if draft_id:
                    submitted_draft_ids.add(str(draft_id))

        drafts = [
            item
            for item in drafts_map.values()
            if str(item.get("draft_id") or "") not in submitted_draft_ids
        ]
        if search:
            search_lower = search.strip().lower()
            drafts = [
                item
                for item in drafts
                if search_lower in str(item.get("draft_name") or "").lower()
                or search_lower in str(item.get("first_product_title") or "").lower()
            ]

        reverse = sort_dir.lower() != "asc"
        if sort_by == "name":
            drafts.sort(
                key=lambda item: (
                    str(
                        item.get("draft_name") or item.get("first_product_title") or ""
                    ).lower()
                ),
                reverse=reverse,
            )
        else:
            drafts.sort(key=lambda item: item.get("created_at") or "", reverse=reverse)
        return drafts[offset : offset + limit]

    def get_product_draft(self, draft_id: str) -> dict[str, Any] | None:
        memory_draft = self.product_drafts.get(draft_id)
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("product_drafts")
                    .select("*")
                    .eq("draft_id", draft_id)
                    .limit(1)
                    .execute()
                )
                rows = res.data or []
                if rows:
                    if memory_draft:
                        return {**rows[0], **memory_draft}
                    return rows[0]
            except Exception:
                LOG.exception("Failed fetching product draft %s", draft_id)

        return memory_draft

    def delete_product_draft(self, draft_id: str) -> bool:
        deleted = False
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("product_drafts")
                    .delete()
                    .eq("draft_id", draft_id)
                    .execute()
                )
                deleted = bool(res.data)
            except Exception:
                LOG.exception("Failed deleting product draft %s", draft_id)
        if draft_id in self.product_drafts:
            del self.product_drafts[draft_id]
            deleted = True
        return deleted

    def save_submitted_document(
        self,
        *,
        submitted_id: str,
        run_id: str | None,
        draft_id: str | None,
        name: str,
        import_mode: str,
        product_count: int,
        products: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = self._utc_now()
        payload = {
            "submitted_id": submitted_id,
            "run_id": run_id,
            "draft_id": draft_id,
            "name": name,
            "import_mode": import_mode,
            "product_count": product_count,
            "products": products,
            "submitted_at": now,
            "created_at": now,
            "updated_at": now,
        }
        client = self._get_supabase_client()
        if client:
            try:
                client.table("submitted_documents").upsert(
                    payload, on_conflict="submitted_id"
                ).execute()
                return payload
            except Exception:
                LOG.exception("Failed saving submitted document %s", submitted_id)
                try:
                    compat_payload = dict(payload)
                    compat_payload.pop("draft_id", None)
                    client.table("submitted_documents").upsert(
                        compat_payload, on_conflict="submitted_id"
                    ).execute()
                except Exception:
                    LOG.exception(
                        "Fallback save for submitted document %s also failed",
                        submitted_id,
                    )
        self.submitted_documents[submitted_id] = payload
        return payload

    def list_submitted_documents(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        sort_by: str = "date",
        sort_dir: str = "desc",
    ) -> list[dict[str, Any]]:
        db_docs: list[dict[str, Any]] = []
        db_docs_loaded = False
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("submitted_documents")
                    .select("*")
                    .limit(1000)
                    .execute()
                )
                db_docs = res.data or []
                db_docs_loaded = True
            except Exception:
                LOG.exception("Failed listing submitted documents")

        if db_docs_loaded:
            docs = [
                item for item in db_docs if item.get("submitted_id")
            ]
        else:
            docs = list(self.submitted_documents.values())
        for doc in docs:
            preview_file_id = doc.get("preview_file_id")
            resolved_preview = (
                preview_file_id
                if isinstance(preview_file_id, str) and preview_file_id
                else None
            )
            if not resolved_preview:
                draft_id = doc.get("draft_id")
                if isinstance(draft_id, str) and draft_id:
                    linked_draft = self.get_product_draft(draft_id)
                    if isinstance(linked_draft, dict):
                        for key in ("output_file_id", "input_file_id"):
                            candidate = linked_draft.get(key)
                            if isinstance(candidate, str) and candidate:
                                resolved_preview = candidate
                                break
            doc["preview_file_id"] = resolved_preview

        if search:
            search_lower = search.strip().lower()
            docs = [
                doc
                for doc in docs
                if search_lower in str(doc.get("name") or "").lower()
            ]

        reverse = sort_dir.lower() != "asc"
        if sort_by == "name":
            docs.sort(
                key=lambda doc: str(doc.get("name") or "").lower(), reverse=reverse
            )
        else:
            docs.sort(
                key=lambda doc: doc.get("submitted_at") or doc.get("created_at") or "",
                reverse=reverse,
            )
        return docs[offset : offset + limit]

    def get_submitted_document(self, submitted_id: str) -> dict[str, Any] | None:
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("submitted_documents")
                    .select("*")
                    .eq("submitted_id", submitted_id)
                    .limit(1)
                    .execute()
                )
                rows = res.data or []
                if rows:
                    return rows[0]
            except Exception:
                LOG.exception("Failed fetching submitted document %s", submitted_id)
        return self.submitted_documents.get(submitted_id)

    def delete_submitted_document(self, submitted_id: str) -> bool:
        deleted = False
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("submitted_documents")
                    .delete()
                    .eq("submitted_id", submitted_id)
                    .execute()
                )
                deleted = bool(res.data)
            except Exception:
                LOG.exception("Failed deleting submitted document %s", submitted_id)
        if submitted_id in self.submitted_documents:
            del self.submitted_documents[submitted_id]
            deleted = True
        return deleted

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
                    .select("*")
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
            **SupabaseService._default_product_intelligence_normalization_settings(),
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

    # ----- LLM model config -----
    @staticmethod
    def _mask_api_key(raw_key: str | None) -> str | None:
        if not raw_key:
            return None
        if len(raw_key) <= 4:
            return "*" * len(raw_key)
        return f"{'*' * max(8, len(raw_key) - 4)}{raw_key[-4:]}"

    @staticmethod
    def _fernet_key_from_env(secret: str) -> bytes:
        if secret.startswith("gAAAA") or len(secret) == 44:
            try:
                return secret.encode("utf-8")
            except Exception as exc:
                raise RuntimeError("Invalid LLM_CONFIG_ENCRYPTION_KEY format") from exc
        digest = sha256(secret.encode("utf-8")).digest()
        return urlsafe_b64encode(digest)

    def _get_cipher(self) -> Fernet:
        raw = (
            os.getenv("LLM_CONFIG_ENCRYPTION_KEY", "").strip()
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
            or os.getenv("SUPABASE_SERVICE_KEY", "").strip()
        )
        if not raw:
            raise RuntimeError(
                "LLM config encryption requires LLM_CONFIG_ENCRYPTION_KEY (or SUPABASE service key env)"
            )
        return Fernet(self._fernet_key_from_env(raw))

    def _encrypt_api_key(self, api_key: str) -> str:
        if not api_key:
            return ""
        cipher = self._get_cipher()
        return cipher.encrypt(api_key.encode("utf-8")).decode("utf-8")

    def _decrypt_api_key(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        cipher = self._get_cipher()
        return cipher.decrypt(ciphertext.encode("utf-8")).decode("utf-8")

    def _sanitize_llm_model_config(self, row: dict[str, Any]) -> dict[str, Any]:
        masked = dict(row)
        masked.pop("api_key_ciphertext", None)
        masked["api_key_masked"] = self._mask_api_key(masked.pop("api_key", None)) or (
            f"************{masked.get('api_key_last4')}"
            if masked.get("api_key_last4")
            else None
        )
        return masked

    def list_llm_model_configs(self, shop_domain: str) -> list[dict[str, Any]]:
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("llm_model_configs")
                    .select("*")
                    .eq("shop_domain", shop_domain)
                    .order("created_at", desc=True)
                    .execute()
                )
                return [
                    self._sanitize_llm_model_config(item) for item in (res.data or [])
                ]
            except Exception:
                LOG.exception(
                    "Failed listing llm_model_configs for shop=%s", shop_domain
                )
        return [
            self._sanitize_llm_model_config(item)
            for item in self.llm_model_configs.values()
            if item.get("shop_domain") == shop_domain
        ]

    def create_llm_model_config(
        self,
        *,
        shop_domain: str,
        name: str,
        provider: str,
        base_url: str,
        model_id: str,
        api_key: str,
        version: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: int | None = None,
        is_active: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = self._utc_now()
        config_id = str(uuid.uuid4())
        payload = {
            "id": config_id,
            "shop_domain": shop_domain,
            "name": name,
            "provider": provider,
            "base_url": base_url,
            "model_id": model_id,
            "version": version,
            "api_key_ciphertext": self._encrypt_api_key(api_key),
            "api_key_last4": api_key[-4:] if api_key else None,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout_seconds": timeout_seconds,
            "is_active": bool(is_active),
            "extra": extra or {},
            "created_at": now,
            "updated_at": now,
        }
        client = self._get_supabase_client()
        if client:
            try:
                client.table("llm_model_configs").insert(payload).execute()
                if is_active:
                    activated = self.activate_llm_model_config(
                        config_id, shop_domain=shop_domain
                    )
                    if activated:
                        return activated
                return self._sanitize_llm_model_config(payload)
            except Exception:
                LOG.exception(
                    "Failed creating llm_model_config for shop=%s", shop_domain
                )

        if is_active:
            for item in self.llm_model_configs.values():
                if item.get("shop_domain") == shop_domain:
                    item["is_active"] = False
        self.llm_model_configs[config_id] = payload
        return self._sanitize_llm_model_config(payload)

    def update_llm_model_config(
        self,
        config_id: str,
        *,
        name: str | None = None,
        provider: str | None = None,
        base_url: str | None = None,
        model_id: str | None = None,
        api_key: str | None = None,
        version: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        update_fields: dict[str, Any] = {"updated_at": self._utc_now()}
        if name is not None:
            update_fields["name"] = name
        if provider is not None:
            update_fields["provider"] = provider
        if base_url is not None:
            update_fields["base_url"] = base_url
        if model_id is not None:
            update_fields["model_id"] = model_id
        if version is not None:
            update_fields["version"] = version
        if temperature is not None:
            update_fields["temperature"] = temperature
        if max_tokens is not None:
            update_fields["max_tokens"] = max_tokens
        if timeout_seconds is not None:
            update_fields["timeout_seconds"] = timeout_seconds
        if extra is not None:
            update_fields["extra"] = extra
        if api_key is not None:
            update_fields["api_key_ciphertext"] = self._encrypt_api_key(api_key)
            update_fields["api_key_last4"] = api_key[-4:] if api_key else None

        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("llm_model_configs")
                    .update(update_fields)
                    .eq("id", config_id)
                    .execute()
                )
                rows = res.data or []
                return self._sanitize_llm_model_config(rows[0]) if rows else None
            except Exception:
                LOG.exception("Failed updating llm_model_config=%s", config_id)

        config = self.llm_model_configs.get(config_id)
        if not config:
            return None
        config.update(update_fields)
        return self._sanitize_llm_model_config(config)

    def delete_llm_model_config(self, config_id: str, *, shop_domain: str) -> bool:
        deleted = False
        client = self._get_supabase_client()
        if client:
            try:
                res = (
                    client.table("llm_model_configs")
                    .delete()
                    .eq("id", config_id)
                    .eq("shop_domain", shop_domain)
                    .execute()
                )
                deleted = bool(res.data)
            except Exception:
                LOG.exception("Failed deleting llm_model_config=%s", config_id)
        config = self.llm_model_configs.get(config_id)
        if config and config.get("shop_domain") == shop_domain:
            del self.llm_model_configs[config_id]
            deleted = True
        return deleted

    def activate_llm_model_config(
        self, config_id: str, *, shop_domain: str
    ) -> dict[str, Any] | None:
        client = self._get_supabase_client()
        if client:
            try:
                client.table("llm_model_configs").update({"is_active": False}).eq(
                    "shop_domain", shop_domain
                ).execute()
                res = (
                    client.table("llm_model_configs")
                    .update({"is_active": True, "updated_at": self._utc_now()})
                    .eq("id", config_id)
                    .eq("shop_domain", shop_domain)
                    .execute()
                )
                rows = res.data or []
                return self._sanitize_llm_model_config(rows[0]) if rows else None
            except Exception:
                LOG.exception("Failed activating llm_model_config=%s", config_id)

        target = None
        for item in self.llm_model_configs.values():
            if item.get("shop_domain") == shop_domain:
                item["is_active"] = False
                if item.get("id") == config_id:
                    target = item
        if not target:
            return None
        target["is_active"] = True
        target["updated_at"] = self._utc_now()
        return self._sanitize_llm_model_config(target)

    def get_active_llm_model_config(self, shop_domain: str) -> dict[str, Any] | None:
        client = self._get_supabase_client()
        row: dict[str, Any] | None = None
        if client:
            try:
                res = (
                    client.table("llm_model_configs")
                    .select("*")
                    .eq("shop_domain", shop_domain)
                    .eq("is_active", True)
                    .limit(1)
                    .execute()
                )
                rows = res.data or []
                row = rows[0] if rows else None
            except Exception:
                LOG.exception(
                    "Failed fetching active llm_model_config for shop=%s", shop_domain
                )
        else:
            for item in self.llm_model_configs.values():
                if item.get("shop_domain") == shop_domain and item.get("is_active"):
                    row = item
                    break

        if not row:
            return None
        try:
            return {
                **row,
                "api_key": self._decrypt_api_key(row.get("api_key_ciphertext") or ""),
            }
        except Exception:
            LOG.exception(
                "Failed decrypting llm model config key for shop=%s", shop_domain
            )
            return None
