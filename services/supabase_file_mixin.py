import logging
from collections.abc import Mapping, Sequence
from typing import Any, TYPE_CHECKING, cast

if TYPE_CHECKING:
    from storage3._sync.file_api import SyncBucketProxy
    from supabase._sync.client import SyncClient

from .supabase_constants import (
    FILE_ORIGIN_DRAFT_RESUME,
    FILE_ORIGIN_MERCHANT_UPLOAD,
    FILE_ORIGIN_SOURCE_HIGHLIGHT,
    FILE_ORIGIN_SUBMITTED_RESUME,
    FILE_ORIGIN_WORKFLOW_OUTPUT,
)

LOG = logging.getLogger(__name__)


class SupabaseFileMixin:
    # Provided by the concrete service (`SupabaseService.__init__`).
    bucket_name: str
    file_storage: dict[str, dict[str, Any]]

    @staticmethod
    def _thumbnail_path(file_id: str) -> str:
        return f"thumbnails/{file_id}.png"

    def _try_get_bucket(self) -> "SyncBucketProxy | None":
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

    def _get_supabase_client(self) -> "SyncClient | None":
        try:
            from supabase_client import get_supabase

            return get_supabase()
        except Exception:
            LOG.debug("Supabase client unavailable", exc_info=True)
            return None

    def _utc_now(self) -> str:
        """Stub for typing — actual implementation provided by `SupabaseRunsMixin`."""
        raise NotImplementedError("_utc_now must be provided by the host class")

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
        return cls._normalize_file_origin(
            row.get("file_origin")
        ) or cls._infer_file_origin_from_filename(row.get("filename"))

    @classmethod
    def _dedupe_and_filter_document_rows(
        cls, rows: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        deduped: dict[str, dict[str, Any]] = {}
        for item in rows:
            storage_path = str(
                item.get("storage_path") or item.get("file_id") or ""
            ).strip()
            if not storage_path:
                continue
            current = deduped.get(storage_path)
            if current is None:
                deduped[storage_path] = dict(item)
                continue
            current_created = str(current.get("created_at") or "")
            candidate_created = str(item.get("created_at") or "")
            if candidate_created >= current_created:
                deduped[storage_path] = dict(item)

        visible_rows: list[dict[str, Any]] = []
        for item in deduped.values():
            item_dict = dict(item)
            if cls._normalize_file_row_origin(item_dict) != FILE_ORIGIN_MERCHANT_UPLOAD:
                continue
            visible_rows.append(
                {
                    **item_dict,
                    "file_id": str(
                        item_dict.get("storage_path") or item_dict.get("file_id") or ""
                    ),
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
        shop_domain: str | None = None,
    ) -> None:
        self.save_files(
            [
                {
                    "file_id": file_id,
                    "name": name,
                    "content": content,
                    "content_type": content_type,
                    "file_origin": file_origin,
                    "shop_domain": shop_domain,
                }
            ]
        )

    def save_files(self, files: Sequence[Mapping[str, Any]]) -> None:
        if not files:
            return

        bucket = self._try_get_bucket()
        metadata_payloads: list[dict[str, Any]] = []
        legacy_metadata_payloads: list[dict[str, Any]] = []
        persisted_file_ids: list[str] = []

        for item in files:
            file_id = str(item.get("file_id") or "").strip()
            if not file_id:
                continue
            name = str(item.get("name") or "").strip() or file_id
            content = item.get("content")
            if not isinstance(content, (bytes, bytearray, memoryview)):
                raise ValueError(f"Invalid content payload for file_id={file_id}")
            content_bytes = bytes(content)
            raw_content_type = item.get("content_type")
            content_type = (
                raw_content_type
                if isinstance(raw_content_type, str) and raw_content_type
                else "application/octet-stream"
            )
            normalized_origin = self._normalize_file_origin(
                item.get("file_origin")
            ) or self._infer_file_origin_from_filename(name)
            normalized_shop = str(item.get("shop_domain") or "").strip().lower() or None

            if bucket is None:
                self.file_storage[file_id] = {
                    "name": name,
                    "content": content_bytes,
                    "content_type": content_type,
                    "storage_path": file_id,
                    "file_origin": normalized_origin,
                    "shop_domain": normalized_shop,
                    "thumbnail_storage_path": None,
                    "thumbnail_content": None,
                }
                persisted_file_ids.append(file_id)
            else:
                safe_content_type = content_type
                if safe_content_type.startswith("text/"):
                    safe_content_type = "application/octet-stream"

                try:
                    bucket.upload(
                        file_id,
                        content_bytes,
                        {
                            "content-type": safe_content_type,
                            "metadata": {"name": name},
                        },
                        )
                    persisted_file_ids.append(file_id)
                except Exception:
                    try:
                        bucket.update(
                            file_id,
                            content_bytes,
                            {
                                "content-type": safe_content_type,
                                "metadata": {"name": name},
                            },
                        )
                    except Exception:
                        bucket.upload(
                            file_id,
                            content_bytes,
                            {"content-type": safe_content_type, "upsert": "true"},
                        )
                    persisted_file_ids.append(file_id)

            payload = {
                "storage_path": file_id,
                "filename": name,
                "content_type": content_type,
                "size": len(content_bytes),
                "file_origin": normalized_origin,
            }
            if normalized_shop:
                payload["shop_domain"] = normalized_shop
            metadata_payloads.append(payload)
            legacy_payload = dict(payload)
            legacy_payload.pop("file_origin", None)
            legacy_metadata_payloads.append(legacy_payload)

        if not metadata_payloads:
            return

        client = self._get_supabase_client()
        if not client:
            return
        try:
            try:
                client.table("file_metadata").upsert(
                    metadata_payloads, on_conflict="storage_path"
                ).execute()
            except Exception:
                try:
                    client.table("file_metadata").upsert(
                        legacy_metadata_payloads, on_conflict="storage_path"
                    ).execute()
                except Exception:
                    for payload in legacy_metadata_payloads:
                        try:
                            client.table("file_metadata").upsert(
                                payload, on_conflict="storage_path"
                            ).execute()
                        except Exception:
                            client.table("file_metadata").insert(payload).execute()
        except Exception:
            LOG.exception("Failed inserting file metadata for bulk save")
            if bucket is not None and persisted_file_ids:
                try:
                    bucket.remove(persisted_file_ids)
                except Exception:
                    LOG.exception("Failed rolling back uploaded storage objects")
            for file_id in persisted_file_ids:
                self.file_storage.pop(file_id, None)
            raise

    def list_files(
        self,
        limit: int = 100,
        offset: int = 0,
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_shop = str(shop_domain or "").strip().lower() or None
        try:
            client = self._get_supabase_client()
            if client:
                db_limit = min(max(limit + offset, 1000), 5000)
                query = client.table("file_metadata").select("*")
                if normalized_shop:
                    query = query.eq("shop_domain", normalized_shop)
                res = query.order("created_at", desc=True).limit(db_limit).execute()
                raw_rows = res.data or []
                normalized_rows = [
                    cast(Mapping[str, Any], row)
                    for row in raw_rows
                    if isinstance(row, Mapping)
                ]
                rows = self._dedupe_and_filter_document_rows(normalized_rows)
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
                    "shop_domain": v.get("shop_domain"),
                    "created_at": v.get("created_at") or self._utc_now(),
                    "thumbnail_storage_path": v.get("thumbnail_storage_path"),
                }
                for k, v in self.file_storage.items()
            ]
            filtered_rows = self._dedupe_and_filter_document_rows(rows)
            if normalized_shop:
                filtered_rows = [
                    row
                    for row in filtered_rows
                    if str(row.get("shop_domain") or "").strip().lower() == normalized_shop
                ]
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
            if data is None:
                return None
            if isinstance(data, (bytes, bytearray, memoryview)):
                content = bytes(data)
            elif hasattr(data, "read"):
                content = data.read()
            else:
                content = data
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
                primary_meta = next(
                    (
                        cast(Mapping[str, Any], row)
                        for row in rows
                        if isinstance(row, Mapping)
                    ),
                    None,
                )
                if primary_meta is not None:
                    meta_dict = dict(primary_meta)
                    name = meta_dict.get("filename", name)
                    content_type = meta_dict.get("content_type", content_type)
                    file_origin = self._normalize_file_row_origin(meta_dict)
                    thumbnail_storage_path = meta_dict.get("thumbnail_storage_path")
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

    def set_file_shop_domain(self, file_id: str, shop_domain: str) -> None:
        normalized_shop = str(shop_domain or "").strip().lower()
        if not normalized_shop:
            raise ValueError("shop_domain is required")
        if file_id in self.file_storage:
            self.file_storage[file_id]["shop_domain"] = normalized_shop
        client = self._get_supabase_client()
        if client:
            client.table("file_metadata").update({"shop_domain": normalized_shop}).eq(
                "storage_path", file_id
            ).execute()

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
            if isinstance(data, (bytes, bytearray, memoryview)):
                return bytes(data)
            if hasattr(data, "read"):
                return data.read()
            return data
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
                candidate = next(
                    (
                        cast(Mapping[str, Any], row)
                        for row in existing_rows
                        if isinstance(row, Mapping)
                    ),
                    None,
                )
                if candidate is not None:
                    thumbnail_path = candidate.get("thumbnail_storage_path")
                client.table("file_metadata").delete().eq(
                    "storage_path", file_id
                ).execute()
        except Exception:
            LOG.warning("Failed deleting metadata for %s", file_id, exc_info=True)

        if isinstance(thumbnail_path, str):
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
