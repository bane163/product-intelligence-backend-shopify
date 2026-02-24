import logging
from typing import Any

from .supabase_constants import (
    FILE_ORIGIN_DRAFT_RESUME,
    FILE_ORIGIN_MERCHANT_UPLOAD,
    FILE_ORIGIN_SOURCE_HIGHLIGHT,
    FILE_ORIGIN_SUBMITTED_RESUME,
    FILE_ORIGIN_WORKFLOW_OUTPUT,
)

LOG = logging.getLogger(__name__)


class SupabaseFileMixin:
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
