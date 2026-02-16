from typing import Any


class SupabaseAdapter:
    """Adapter that proxies calls to the real SupabaseService instance.

    This keeps the application layer depending on an interface/port while
    reusing the existing SupabaseService implementation.
    """

    def __init__(self, service: Any) -> None:
        self._service = service

    def __getattr__(self, name: str):
        return getattr(self._service, name)
