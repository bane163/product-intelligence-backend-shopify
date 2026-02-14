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
