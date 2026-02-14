import logging
import os
from datetime import datetime, timezone
from typing import Any

from objects.sanitize import sanitize_text
from .interfaces import SupabaseServiceInterface

LOG = logging.getLogger(__name__)


class SupabaseService(SupabaseServiceInterface):
    def __init__(self, bucket_name: str | None = None):
        self.bucket_name = bucket_name or os.environ.get("FILES_BUCKET_NAME", "documents")
        self.file_storage: dict[str, dict[str, Any]] = {}
        self.product_drafts: dict[str, dict[str, Any]] = {}
        self.submitted_documents: dict[str, dict[str, Any]] = {}

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

    # ----- File storage -----
    def save_file(
        self, file_id: str, name: str, content: bytes, content_type: str | None = None
    ) -> None:
        bucket = self._try_get_bucket()
        if bucket is None:
            self.file_storage[file_id] = {
                "name": name,
                "content": content,
                "content_type": content_type or "application/octet-stream",
                "storage_path": file_id,
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
            bucket.upload(file_id, content, {"content-type": safe_content_type})

        try:
            client = self._get_supabase_client()
            if client:
                client.table("file_metadata").insert(
                    {
                        "storage_path": file_id,
                        "filename": name,
                        "content_type": content_type or "application/octet-stream",
                        "size": len(content) if content else 0,
                    }
                ).execute()
        except Exception:
            LOG.exception("Failed inserting file metadata for %s", file_id)

    def list_files(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        try:
            client = self._get_supabase_client()
            if client:
                res = (
                    client.table("file_metadata")
                    .select("*")
                    .order("created_at", desc=True)
                    .range(offset, offset + limit - 1)
                    .execute()
                )
                return res.data or []
        except Exception:
            LOG.exception("Failed listing files from DB")

        bucket = self._try_get_bucket()
        if bucket is None:
            return [
                {
                    "file_id": k,
                    "storage_path": k,
                    "filename": v["name"],
                    "content_type": v["content_type"],
                }
                for k, v in self.file_storage.items()
            ]

        try:
            files = bucket.list(path=None)
            return [
                {
                    "file_id": f.get("name"),
                    "storage_path": f.get("name"),
                    "filename": f.get("metadata", {}).get("name", f.get("name")),
                    "content_type": f.get("metadata", {}).get("mimetype"),
                    "size": f.get("metadata", {}).get("size"),
                    "created_at": f.get("created_at"),
                }
                for f in files
            ]
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
        try:
            client = self._get_supabase_client()
            if client:
                res = (
                    client.table("file_metadata")
                    .select("*")
                    .eq("storage_path", file_id)
                    .limit(1)
                    .execute()
                )
                rows = res.data or []
                if rows:
                    meta = rows[0]
                    name = meta.get("filename", name)
                    content_type = meta.get("content_type", content_type)
        except Exception:
            LOG.debug("DB metadata fetch failed for %s", file_id, exc_info=True)

        return {
            "name": name,
            "content": content,
            "content_type": content_type,
            "storage_path": file_id,
        }

    def delete_file(self, file_id: str) -> bool:
        bucket = self._try_get_bucket()
        if bucket is None:
            if file_id in self.file_storage:
                del self.file_storage[file_id]
                return True
            return False

        try:
            client = self._get_supabase_client()
            if client:
                client.table("file_metadata").delete().eq("storage_path", file_id).execute()
        except Exception:
            LOG.warning("Failed deleting metadata for %s", file_id, exc_info=True)

        try:
            bucket.remove([file_id])
            return True
        except Exception:
            return False

    # ----- Run logging -----
    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_or_update_run(self, run_id: str, fields: dict[str, Any]) -> None:
        client = self._get_supabase_client()
        if not client:
            return
        payload = {"run_id": run_id, **fields}
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
        fields: dict[str, Any] = {
            "status": status,
            "ended_at": self._utc_now(),
            "duration_ms": duration_ms,
        }
        if error:
            fields["error"] = error
        if extra_fields:
            fields.update(extra_fields)
        self.create_or_update_run(run_id, fields)

    def list_runs(
        self, limit: int = 50, offset: int = 0, status: str | None = None
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
            res = query.execute()
            return res.data or []
        except Exception:
            LOG.exception("Failed listing llm_runs")
            return []

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        client = self._get_supabase_client()
        if not client:
            return None
        try:
            res = client.table("llm_runs").select("*").eq("run_id", run_id).limit(1).execute()
            rows = res.data or []
            return rows[0] if rows else None
        except Exception:
            LOG.exception("Failed fetching llm_runs for run_id=%s", run_id)
            return None

    def get_run_history(self, run_id: str) -> dict[str, Any]:
        client = self._get_supabase_client()
        if not client:
            return {"run": None, "events": [], "messages": []}
        run = self.get_run(run_id)
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
            "products": products,
            "product_count": len(products),
            "first_product_title": first_title,
            "created_at": now,
            "updated_at": now,
        }
        client = self._get_supabase_client()
        if client:
            try:
                client.table("product_drafts").upsert(payload, on_conflict="draft_id").execute()
                return payload
            except Exception:
                LOG.exception("Failed saving product draft %s", draft_id)
                try:
                    compat_payload = dict(payload)
                    compat_payload.pop("first_product_title", None)
                    compat_payload.pop("draft_name", None)
                    client.table("product_drafts").upsert(
                        compat_payload, on_conflict="draft_id"
                    ).execute()
                except Exception:
                    LOG.exception("Fallback save for product draft %s also failed", draft_id)
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
            except Exception:
                LOG.exception("Failed listing product drafts")
            try:
                submitted_res = (
                    client.table("submitted_documents").select("draft_id").limit(1000).execute()
                )
                for item in submitted_res.data or []:
                    draft_id = item.get("draft_id")
                    if draft_id:
                        submitted_draft_ids.add(str(draft_id))
            except Exception:
                LOG.debug("Submitted documents table unavailable for draft filtering", exc_info=True)

        drafts_map: dict[str, dict[str, Any]] = {
            str(item.get("draft_id")): item for item in db_drafts if item.get("draft_id")
        }
        for key, item in self.product_drafts.items():
            drafts_map[str(item.get("draft_id") or key)] = item

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
                    str(item.get("draft_name") or item.get("first_product_title") or "").lower()
                ),
                reverse=reverse,
            )
        else:
            drafts.sort(key=lambda item: item.get("created_at") or "", reverse=reverse)
        return drafts[offset : offset + limit]

    def get_product_draft(self, draft_id: str) -> dict[str, Any] | None:
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
                return rows[0] if rows else None
            except Exception:
                LOG.exception("Failed fetching product draft %s", draft_id)

        return self.product_drafts.get(draft_id)

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
                client.table("submitted_documents").upsert(payload, on_conflict="submitted_id").execute()
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
                    LOG.exception("Fallback save for submitted document %s also failed", submitted_id)
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
        client = self._get_supabase_client()
        if client:
            try:
                res = client.table("submitted_documents").select("*").limit(1000).execute()
                db_docs = res.data or []
            except Exception:
                LOG.exception("Failed listing submitted documents")

        docs_map: dict[str, dict[str, Any]] = {
            str(item.get("submitted_id")): item for item in db_docs if item.get("submitted_id")
        }
        for key, item in self.submitted_documents.items():
            docs_map[str(item.get("submitted_id") or key)] = item

        docs = list(docs_map.values())
        if search:
            search_lower = search.strip().lower()
            docs = [doc for doc in docs if search_lower in str(doc.get("name") or "").lower()]

        reverse = sort_dir.lower() != "asc"
        if sort_by == "name":
            docs.sort(key=lambda doc: str(doc.get("name") or "").lower(), reverse=reverse)
        else:
            docs.sort(key=lambda doc: doc.get("submitted_at") or doc.get("created_at") or "", reverse=reverse)
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
