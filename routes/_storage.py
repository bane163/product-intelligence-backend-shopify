"""Storage adapter for uploaded files.

This module exposes the same API as the previous in-memory helper:
- `save_file(file_id, name, content, content_type)`
- `get_file(file_id)` -> Optional[dict(name, content, content_type)]
- `delete_file(file_id)` -> bool
- `list_files(limit, offset)` -> List[Dict]

The implementation now persists file bytes to a Supabase Storage bucket and
reads metadata where possible. Environment variable `FILES_BUCKET_NAME` can be
used to configure the bucket name (default: "documents").
"""

import os
import logging
from typing import Dict, Any, Optional

LOG = logging.getLogger(__name__)

BUCKET_NAME = os.environ.get("FILES_BUCKET_NAME", "documents")

# In-memory fallback storage used when Supabase is not configured or unavailable.
file_storage: Dict[str, Dict[str, Any]] = {}


def _try_get_bucket():
    """Attempt to return a Supabase bucket object or None if unavailable.

    This avoids failing at import time when env vars or client libs are not present.
    """
    # Attempt relative import (when code is a package) first, then fall back to
    # absolute import which works when running the app as a top-level module.
    try:
        # import here to avoid import-time errors when running tests without envs
        from ..supabase_client import get_storage as _get_storage  # type: ignore
    except Exception:
        try:
            import supabase_client as _supabase_client  # type: ignore

            _get_storage = _supabase_client.get_storage  # type: ignore[attr-defined]
        except Exception:
            LOG.exception(
                "Failed to import Supabase storage helper (relative and absolute)"
            )
            return None

    try:
        storage = _get_storage()
        bucket = storage.from_(BUCKET_NAME)
        LOG.debug("Connected to Supabase bucket '%s' (%r)", BUCKET_NAME, bucket)
        return bucket
    except Exception:
        LOG.exception("Supabase storage unavailable; falling back to in-memory storage")
        return None



def _get_supabase_client():
    """Attempt to return the Supabase client or None."""
    try:
        from ..supabase_client import get_supabase
        return get_supabase()
    except ImportError:
        try:
            from supabase_client import get_supabase
            return get_supabase()
        except ImportError:
            return None
    except Exception:
        LOG.debug("Supabase client unavailable", exc_info=True)
        return None

def save_file(
    file_id: str, name: str, content: bytes, content_type: Optional[str] = None
) -> None:
    """Store the file bytes in Supabase Storage under key `file_id`.

    Attempts to set basic metadata (`name`, `content-type`) when supported by
    the client library; falls back to a plain upload. Also syncs to `file_metadata` table.
    """
    bucket = _try_get_bucket()
    # If Supabase is not available use in-memory storage for tests/development
    if bucket is None:
        file_storage[file_id] = {
            "name": name,
            "content": content,
            "content_type": content_type or "application/octet-stream",
        }
        return

    path = file_id

    LOG.debug(
        "Saving file to storage: bucket=%s path=%s size=%d content_type=%s",
        BUCKET_NAME,
        path,
        len(content) if content is not None else 0,
        content_type,
    )

    # Try common upload signatures used by supabase client libraries.
    upload_success = False
    try:
        # Preferred: upload with content-type/metadata kwargs
        LOG.debug("Attempting upload with metadata to %s/%s", BUCKET_NAME, path)
        res = bucket.upload(
            path,
            content,
            {
                "content-type": content_type or "application/octet-stream",
                "metadata": {"name": name},
            },
        )
        LOG.info("Upload (with metadata) succeeded for %s; result=%r", path, res)
        upload_success = True
    except Exception as exc:
        LOG.debug("Upload with metadata failed for %s: %s", path, exc, exc_info=True)

    if not upload_success:
        try:
            LOG.debug("Attempting simple upload to %s/%s", BUCKET_NAME, path)
            res = bucket.upload(path, content)
            LOG.info("Upload (simple) succeeded for %s; result=%r", path, res)
            upload_success = True
        except Exception as exc:
            LOG.debug(
                "Failed to upload file %s to bucket %s: %s",
                path,
                BUCKET_NAME,
                exc,
                exc_info=True,
            )
            raise exc

    # Sync to file_metadata table
    if upload_success:
        try:
            client = _get_supabase_client()
            if client:
                client.table("file_metadata").insert({
                    "storage_path": path,
                    "filename": name,
                    "content_type": content_type or "application/octet-stream",
                    "size": len(content) if content else 0
                }).execute()
                LOG.info("Inserted metadata into table for %s", path)
        except Exception:
            LOG.exception("Failed to insert metadata into DB for %s", path)


def list_files(limit: int = 100, offset: int = 0) -> list[Dict[str, Any]]:
    """List files from the file_metadata table.

    Returns a list of dicts with file metadata. Use this for listing
    available files without downloading their content.
    """
    try:
        client = _get_supabase_client()
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
        LOG.exception("Failed to list files from DB")

    # Fallback to listing from bucket if DB fails or isn't populated
    # Note: bucket.list() might not return all metadata depending on the adapter
    bucket = _try_get_bucket()
    if bucket is None:
        # In-memory fallback
        return [
            {"file_id": k, "filename": v["name"], "content_type": v["content_type"]}
            for k, v in file_storage.items()
        ]

    try:
        LOG.debug("Listing files from bucket %s", BUCKET_NAME)
        files = bucket.list(path=None)  # top level
        # Transform bucket list result to match DB schema roughly
        return [
            {
                "file_id": f.get("name"),  # we use file_id as path
                "storage_path": f.get("name"),
                "filename": f.get("metadata", {}).get("name", f.get("name")),
                "content_type": f.get("metadata", {}).get("mimetype"),
                "size": f.get("metadata", {}).get("size"),
                "created_at": f.get("created_at"),
            }
            for f in files
        ]
    except Exception:
        LOG.exception("Failed to list files from bucket")
        return []



def get_file(file_id: str) -> Dict[str, Any] | None:
    """Retrieve a file from storage and return a dict with `name`, `content`, `content_type`.

    Returns None when the object is not present.
    """
    bucket = _try_get_bucket()
    # In-memory fallback
    if bucket is None:
        entry = file_storage.get(file_id)
        return entry

    path = file_id

    try:
        # Many supabase clients provide `.download(path)` returning bytes
        LOG.debug("Attempting download from %s/%s", BUCKET_NAME, path)
        data = bucket.download(path)
    except Exception:
        LOG.debug("download failed for %s", path, exc_info=True)
        return None

    # Normalize bytes
    content = None
    try:
        if hasattr(data, "read"):
            content = data.read()  # type: ignore[attr-defined]
        else:
            content = data
    except Exception:
        LOG.exception("Failed to read downloaded data for %s", path)
        return None

    name = file_id
    content_type = "application/octet-stream"

    # Try reading from file_metadata table first (most reliable)
    try:
        client = _get_supabase_client()
        if client:
            res = client.table("file_metadata").select("*").eq("storage_path", path).execute()
            if res.data and len(res.data) > 0:
                meta_row = res.data[0]
                name = meta_row.get("filename", name)
                content_type = meta_row.get("content_type", content_type)
                LOG.debug("Resolved metadata from DB for %s: name=%s", path, name)
                return {"name": name, "content": content, "content_type": content_type}
    except Exception:
        LOG.debug("DB metadata fetch failed for %s", path, exc_info=True)

    # Fallback to storage metadata
    try:
        LOG.debug("Attempting to fetch storage metadata for %s/%s", BUCKET_NAME, path)
        meta = bucket.get_metadata(path)  # type: ignore[attr-defined]
        if meta:
            # supabase metadata shape varies; try common keys
            name = meta.get("metadata", {}).get("name") or meta.get("name") or name
            content_type = (
                meta.get("content_type")
                or meta.get("mimeType")
                or meta.get("metadata", {}).get("content-type")
                or content_type
            )
    except Exception:
        # Not all clients support get_metadata; that's fine
        LOG.debug("metadata fetch not available for %s", path, exc_info=True)

    return {"name": name, "content": content, "content_type": content_type}


def delete_file(file_id: str) -> bool:
    """Delete object at `file_id`. Returns True if deleted or False if not found."""
    bucket = _try_get_bucket()
    # In-memory fallback
    if bucket is None:
        if file_id in file_storage:
            del file_storage[file_id]
            return True
        return False
    
    # Try deleting from DB first (cleanup)
    try:
        client = _get_supabase_client()
        if client:
            client.table("file_metadata").delete().eq("id", file_id).execute()
    except Exception:
        LOG.warning("Failed to delete metadata from DB for %s", file_id, exc_info=True)

    try:
        bucket.remove([file_id])
        return True
    except Exception:
        LOG.debug("delete failed or file not found: %s", file_id, exc_info=True)
        return False

