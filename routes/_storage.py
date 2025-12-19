"""Storage adapter for uploaded files.

This module exposes the same API as the previous in-memory helper:
- `save_file(file_id, name, content, content_type)`
- `get_file(file_id)` -> Optional[dict(name, content, content_type)]
- `delete_file(file_id)` -> bool

The implementation now persists file bytes to a Supabase Storage bucket and
reads metadata where possible. Environment variable `FILES_BUCKET_NAME` can be
used to configure the bucket name (default: "files").
"""

import os
import logging
from typing import Dict, Any, Optional

LOG = logging.getLogger(__name__)

BUCKET_NAME = os.environ.get("FILES_BUCKET_NAME", "files")

# In-memory fallback storage used when Supabase is not configured or unavailable.
file_storage: Dict[str, Dict[str, Any]] = {}


def _try_get_bucket():
    """Attempt to return a Supabase bucket object or None if unavailable.

    This avoids failing at import time when env vars or client libs are not present.
    """
    try:
        # import here to avoid import-time errors when running tests without envs
        from ..supabase_client import get_storage

        storage = get_storage()
        return storage.from_(BUCKET_NAME)
    except Exception:
        LOG.debug(
            "Supabase storage unavailable; using in-memory fallback", exc_info=True
        )
        return None


def save_file(
    file_id: str, name: str, content: bytes, content_type: Optional[str] = None
) -> None:
    """Store the file bytes in Supabase Storage under key `file_id`.

    Attempts to set basic metadata (`name`, `content-type`) when supported by
    the client library; falls back to a plain upload.
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

    # Try common upload signatures used by supabase client libraries.
    try:
        # Preferred: upload with content-type/metadata kwargs
        bucket.upload(
            path,
            content,
            {
                "content-type": content_type or "application/octet-stream",
                "metadata": {"name": name},
            },
        )
        return
    except Exception:
        LOG.debug("upload with metadata failed, trying simple upload", exc_info=True)

    try:
        # Fallback: basic upload
        bucket.upload(path, content)
        # Metadata may not be set in this fallback
        return
    except Exception:
        LOG.exception("Failed to upload file %s to bucket %s", path, BUCKET_NAME)
        raise


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

    # Try reading metadata if available
    try:
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
        LOG.debug("metadata fetch not available for %s", path)

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

    path = file_id
    try:
        # Many clients accept a list of paths to remove
        bucket.remove([path])
        return True
    except Exception:
        LOG.debug("delete failed or file not found: %s", path, exc_info=True)
        return False
