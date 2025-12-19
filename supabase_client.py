import os
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from supabase import Client
    from storage3 import SyncStorageClient

try:
    from supabase import create_client
except Exception:  # pragma: no cover - runtime dependency
    create_client = None  # type: ignore

_SUPABASE_CLIENT: Optional["Client"] = None


def get_supabase() -> "Client":
    """Return a cached Supabase client initialized from env vars.

    Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_ANON_KEY).
    """
    global _SUPABASE_CLIENT
    if _SUPABASE_CLIENT is not None:
        return _SUPABASE_CLIENT

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get(
        "SUPABASE_ANON_KEY"
    )
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_ANON_KEY) must be set"
        )

    if create_client is None:
        raise RuntimeError(
            "supabase package is not installed; please install with 'uv add supabase'"
        )

    _SUPABASE_CLIENT = create_client(url, key)
    return _SUPABASE_CLIENT


def get_storage() -> "SyncStorageClient":
    """Return the storage client helper from the Supabase client.

    Usage: bucket = get_storage().from_(bucket_name)
    """
    supabase = get_supabase()
    supabase.storage
    return supabase.storage
