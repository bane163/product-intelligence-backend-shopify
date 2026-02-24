import logging
from typing import Any

LOG = logging.getLogger(__name__)


class SupabaseDraftsMixin:
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
