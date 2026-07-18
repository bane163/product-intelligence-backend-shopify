import logging
from typing import Any, Optional

LOG = logging.getLogger(__name__)


def _iter_exception_texts(exc: Exception) -> list[str]:
    candidates: list[str] = []
    for value in (
        str(exc),
        repr(exc),
        getattr(exc, "message", None),
        getattr(exc, "details", None),
        getattr(exc, "hint", None),
    ):
        if value:
            candidates.append(str(value))
    for arg in getattr(exc, "args", ()) or ():
        if isinstance(arg, dict):
            for key in ("message", "details", "hint", "code"):
                value = arg.get(key)
                if value:
                    candidates.append(str(value))
            candidates.append(str(arg))
        elif arg:
            candidates.append(str(arg))
    return candidates


def _normalize_error_text(value: str) -> str:
    return " ".join(
        value.lower().replace('"', "").replace("'", "").replace("`", "").split()
    )


def _normalize_shop_domain(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _matches_shop_domain(record: dict[str, Any], shop_domain: str | None) -> bool:
    if not shop_domain:
        return True
    record_shop_domain = _normalize_shop_domain(record.get("shop_domain"))
    if not record_shop_domain:
        return False
    return record_shop_domain == shop_domain


def _is_missing_column_error(exc: Exception, *, table: str, column: str) -> bool:
    table_name = table.lower().split(".")[-1]
    column_name = column.lower()
    missing_markers = ("could not find", "does not exist", "not found", "missing")
    for text in _iter_exception_texts(exc):
        message = _normalize_error_text(text)
        if column_name not in message:
            continue
        if not any(marker in message for marker in missing_markers):
            continue
        if f"{table_name}.{column_name}" in message:
            return True
        if f"{column_name} column" in message and (
            table_name in message or "schema cache" in message
        ):
            return True
        if f"column {column_name}" in message:
            return True
    return False


class SupabaseDraftsMixin:
    # Provided by the concrete service (`SupabaseService.__init__`).
    product_drafts: dict[str, dict[str, Any]]
    submitted_documents: dict[str, dict[str, Any]]

    def _get_supabase_client(self) -> Optional[Any]:
        """Stub for static typing.

        Concrete service classes (e.g. `SupabaseService`) typically provide
        `_get_supabase_client` (see `SupabaseFileMixin`). This stub exists so
        type checkers know the attribute is available on `self`.
        """
        raise NotImplementedError(
            "_get_supabase_client must be implemented by the host class"
        )

    def _utc_now(self) -> str:
        """Stub for typing — actual implementation provided by `SupabaseRunsMixin`."""
        raise NotImplementedError("_utc_now must be provided by the host class")

    def _sync_source_references(self, *, products: list[dict[str, Any]], shop_domain: str | None,
                                draft_id: str | None = None, submitted_id: str | None = None) -> None:
        client = self._get_supabase_client()
        if not client or not shop_domain:
            return
        owner_column, owner_value = ("draft_id", draft_id) if draft_id else ("submitted_id", submitted_id)
        if not owner_value:
            return
        rows: list[dict[str, Any]] = []
        for product_index, product in enumerate(products):
            refs = product.get("source_refs") if isinstance(product, dict) else None
            for ref in refs if isinstance(refs, list) else []:
                if not isinstance(ref, dict) or not ref.get("source_file_id"):
                    continue
                rows.append({
                    "shop_domain": shop_domain, owner_column: owner_value,
                    "product_index": product_index, "field_name": ref.get("field"),
                    "source_file_id": ref["source_file_id"], "sheet_name": ref.get("sheet"),
                    "cell_range": ref.get("cell_range") or ref.get("cell"),
                    "page_number": ref.get("page"), "bounding_box": ref.get("bbox"),
                    "anchor_id": ref.get("anchor_id"), "document_kind": ref.get("document_kind"),
                    "source_value": ref.get("value"), "source_provider": ref.get("source_provider"),
                })
        try:
            client.table("product_source_references").delete().eq(owner_column, owner_value).eq("shop_domain", shop_domain).execute()
            if rows:
                client.table("product_source_references").insert(rows).execute()
        except Exception:
            LOG.exception("Failed synchronizing source references for %s", owner_value)

    def save_product_draft(
        self,
        *,
        draft_id: str,
        run_id: str | None,
        import_mode: str,
        draft_name: str | None,
        shop_domain: str | None = None,
        input_file_id: str | None = None,
        input_filename: str | None = None,
        output_file_id: str | None = None,
        output_filename: str | None = None,
        extraction_status: str | None = None,
        extraction_run_id: str | None = None,
        extraction_error: str | None = None,
        submit_status: str | None = None,
        submit_run_id: str | None = None,
        submit_error: str | None = None,
        require_lifecycle_columns: bool = False,
        products: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = self._utc_now()
        normalized_shop_domain = _normalize_shop_domain(shop_domain)
        first_title = ""
        if products and isinstance(products[0], dict):
            first_title = str(products[0].get("title") or "")
        payload = {
            "draft_id": draft_id,
            "run_id": run_id,
            "import_mode": import_mode,
            "draft_name": draft_name,
            "shop_domain": normalized_shop_domain,
            "input_file_id": input_file_id,
            "input_filename": input_filename,
            "output_file_id": output_file_id,
            "output_filename": output_filename,
            "extraction_status": extraction_status,
            "extraction_run_id": extraction_run_id,
            "extraction_error": extraction_error,
            "submit_status": submit_status,
            "submit_run_id": submit_run_id,
            "submit_error": submit_error,
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
                self._sync_source_references(products=products, shop_domain=normalized_shop_domain, draft_id=draft_id)
                return payload
            except Exception as exc:
                if _is_missing_column_error(
                    exc, table="product_drafts", column="draft_name"
                ):
                    try:
                        compat_payload = dict(payload)
                        compat_payload.pop("draft_name", None)
                        client.table("product_drafts").upsert(
                            compat_payload, on_conflict="draft_id"
                        ).execute()
                        return payload
                    except Exception as draft_name_exc:
                        LOG.exception(
                            "Retry without product_drafts.draft_name failed for draft %s",
                            draft_id,
                        )
                        if require_lifecycle_columns:
                            raise RuntimeError(
                                "Draft lifecycle persistence failed (product_drafts)"
                            ) from draft_name_exc
                else:
                    LOG.exception("Failed saving product draft %s", draft_id)
                    if require_lifecycle_columns:
                        raise RuntimeError(
                            "Draft lifecycle persistence failed (product_drafts)"
                        ) from exc
                try:
                    compat_payload = dict(payload)
                    compat_payload.pop("first_product_title", None)
                    compat_payload.pop("draft_name", None)
                    compat_payload.pop("input_file_id", None)
                    compat_payload.pop("input_filename", None)
                    compat_payload.pop("output_file_id", None)
                    compat_payload.pop("output_filename", None)
                    compat_payload.pop("extraction_status", None)
                    compat_payload.pop("extraction_run_id", None)
                    compat_payload.pop("extraction_error", None)
                    compat_payload.pop("submit_status", None)
                    compat_payload.pop("submit_run_id", None)
                    compat_payload.pop("submit_error", None)
                    compat_payload.pop("shop_domain", None)
                    compat_payload.pop("extraction_progress", None)
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
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = max(limit, 0)
        safe_offset = max(offset, 0)
        query_limit = max(safe_limit + safe_offset, 1)
        db_drafts: list[dict[str, Any]] = []
        submitted_draft_ids: set[str] = set()
        db_drafts_loaded = False
        normalized_shop_domain = _normalize_shop_domain(shop_domain)
        client = self._get_supabase_client()
        if client:
            try:
                query = client.table("product_drafts").select("*")
                if normalized_shop_domain:
                    query = query.eq("shop_domain", normalized_shop_domain)
                res = query.order("created_at", desc=True).limit(query_limit).execute()
                db_drafts = res.data or []
                db_drafts_loaded = True
            except Exception as exc:
                if normalized_shop_domain and _is_missing_column_error(
                    exc, table="product_drafts", column="shop_domain"
                ):
                    try:
                        res = (
                            client.table("product_drafts")
                            .select("*")
                            .order("created_at", desc=True)
                            .limit(query_limit)
                            .execute()
                        )
                        db_drafts = res.data or []
                        db_drafts_loaded = True
                    except Exception:
                        LOG.exception("Failed listing product drafts")
                else:
                    LOG.exception("Failed listing product drafts")
            try:
                submitted_query = client.table("submitted_documents").select(
                    "draft_id,shop_domain"
                )
                if normalized_shop_domain:
                    submitted_query = submitted_query.eq(
                        "shop_domain", normalized_shop_domain
                    )
                submitted_res = submitted_query.limit(1000).execute()
                for item in submitted_res.data or []:
                    if not _matches_shop_domain(item, normalized_shop_domain):
                        continue
                    draft_id = item.get("draft_id")
                    if draft_id:
                        submitted_draft_ids.add(str(draft_id))
            except Exception as exc:
                if _is_missing_column_error(
                    exc, table="submitted_documents", column="shop_domain"
                ):
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
                else:
                    LOG.debug(
                        "Submitted documents table unavailable for draft filtering",
                        exc_info=True,
                    )

        if db_drafts_loaded:
            drafts_map: dict[str, dict[str, Any]] = {
                str(item.get("draft_id")): item
                for item in db_drafts
                if item.get("draft_id")
                and _matches_shop_domain(item, normalized_shop_domain)
            }
            for memory_draft in self.product_drafts.values():
                if not isinstance(memory_draft, dict):
                    continue
                if not _matches_shop_domain(memory_draft, normalized_shop_domain):
                    continue
                draft_id = memory_draft.get("draft_id")
                if not draft_id:
                    continue
                key = str(draft_id)
                drafts_map[key] = {**drafts_map.get(key, {}), **memory_draft}
        else:
            drafts_map = {
                str(item.get("draft_id") or key): item
                for key, item in self.product_drafts.items()
                if _matches_shop_domain(item, normalized_shop_domain)
            }

        for item in self.submitted_documents.values():
            if not _matches_shop_domain(item, normalized_shop_domain):
                continue
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
        return drafts[safe_offset : safe_offset + safe_limit]

    def get_product_draft(
        self, draft_id: str, *, shop_domain: str | None = None
    ) -> dict[str, Any] | None:
        memory_draft = self.product_drafts.get(draft_id)
        normalized_shop_domain = _normalize_shop_domain(shop_domain)
        if memory_draft and not _matches_shop_domain(memory_draft, normalized_shop_domain):
            memory_draft = None
        client = self._get_supabase_client()
        if client:
            try:
                query = client.table("product_drafts").select("*").eq("draft_id", draft_id)
                if normalized_shop_domain:
                    query = query.eq("shop_domain", normalized_shop_domain)
                res = query.limit(1).execute()
                rows = res.data or []
                if rows:
                    row = rows[0]
                    if not _matches_shop_domain(row, normalized_shop_domain):
                        return memory_draft
                    if memory_draft:
                        return {**row, **memory_draft}
                    return row
            except Exception as exc:
                if normalized_shop_domain and _is_missing_column_error(
                    exc, table="product_drafts", column="shop_domain"
                ):
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
                            row = rows[0]
                            if not _matches_shop_domain(row, normalized_shop_domain):
                                return memory_draft
                            if memory_draft:
                                return {**row, **memory_draft}
                            return row
                    except Exception:
                        LOG.exception("Failed fetching product draft %s", draft_id)
                else:
                    LOG.exception("Failed fetching product draft %s", draft_id)

        return memory_draft

    def delete_product_draft(
        self, draft_id: str, *, shop_domain: str | None = None
    ) -> bool:
        deleted = False
        normalized_shop_domain = _normalize_shop_domain(shop_domain)
        if normalized_shop_domain and not self.get_product_draft(
            draft_id,
            shop_domain=normalized_shop_domain,
        ):
            return False
        client = self._get_supabase_client()
        if client:
            try:
                query = client.table("product_drafts").delete().eq("draft_id", draft_id)
                if normalized_shop_domain:
                    query = query.eq("shop_domain", normalized_shop_domain)
                res = query.execute()
                deleted_rows = [
                    row
                    for row in (res.data or [])
                    if isinstance(row, dict)
                    and _matches_shop_domain(row, normalized_shop_domain)
                ]
                deleted = bool(deleted_rows)
            except Exception as exc:
                if normalized_shop_domain and _is_missing_column_error(
                    exc, table="product_drafts", column="shop_domain"
                ):
                    try:
                        res = (
                            client.table("product_drafts")
                            .delete()
                            .eq("draft_id", draft_id)
                            .execute()
                        )
                        deleted_rows = [
                            row
                            for row in (res.data or [])
                            if isinstance(row, dict)
                            and _matches_shop_domain(row, normalized_shop_domain)
                        ]
                        deleted = bool(deleted_rows)
                    except Exception:
                        LOG.exception("Failed deleting product draft %s", draft_id)
                else:
                    LOG.exception("Failed deleting product draft %s", draft_id)
        if draft_id in self.product_drafts and (
            _matches_shop_domain(self.product_drafts[draft_id], normalized_shop_domain)
        ):
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
        shop_domain: str | None = None,
        product_count: int,
        products: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = self._utc_now()
        normalized_shop_domain = _normalize_shop_domain(shop_domain)
        payload = {
            "submitted_id": submitted_id,
            "run_id": run_id,
            "draft_id": draft_id,
            "name": name,
            "import_mode": import_mode,
            "shop_domain": normalized_shop_domain,
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
                self._sync_source_references(products=products, shop_domain=normalized_shop_domain, submitted_id=submitted_id)
                return payload
            except Exception:
                LOG.exception("Failed saving submitted document %s", submitted_id)
                try:
                    compat_payload = dict(payload)
                    compat_payload.pop("draft_id", None)
                    compat_payload.pop("shop_domain", None)
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
        shop_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = max(limit, 0)
        safe_offset = max(offset, 0)
        query_limit = max(safe_limit + safe_offset, 1)
        db_docs: list[dict[str, Any]] = []
        db_docs_loaded = False
        normalized_shop_domain = _normalize_shop_domain(shop_domain)
        client = self._get_supabase_client()
        if client:
            try:
                query = client.table("submitted_documents").select("*")
                if normalized_shop_domain:
                    query = query.eq("shop_domain", normalized_shop_domain)
                res = query.limit(query_limit).execute()
                db_docs = res.data or []
                db_docs_loaded = True
            except Exception as exc:
                if normalized_shop_domain and _is_missing_column_error(
                    exc, table="submitted_documents", column="shop_domain"
                ):
                    try:
                        res = (
                            client.table("submitted_documents")
                            .select("*")
                            .limit(query_limit)
                            .execute()
                        )
                        db_docs = res.data or []
                        db_docs_loaded = True
                    except Exception:
                        LOG.exception("Failed listing submitted documents")
                else:
                    LOG.exception("Failed listing submitted documents")

        if db_docs_loaded:
            docs_map = {
                str(item.get("submitted_id")): item
                for item in db_docs
                if item.get("submitted_id")
                and _matches_shop_domain(item, normalized_shop_domain)
            }
            for memory_doc in self.submitted_documents.values():
                if not isinstance(memory_doc, dict):
                    continue
                if not _matches_shop_domain(memory_doc, normalized_shop_domain):
                    continue
                submitted_id = memory_doc.get("submitted_id")
                if not submitted_id:
                    continue
                key = str(submitted_id)
                docs_map[key] = {**docs_map.get(key, {}), **memory_doc}
            docs = list(docs_map.values())
        else:
            docs = [
                item
                for item in self.submitted_documents.values()
                if _matches_shop_domain(item, normalized_shop_domain)
            ]
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
                    linked_draft = self.get_product_draft(
                        draft_id,
                        shop_domain=normalized_shop_domain,
                    )
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
        return docs[safe_offset : safe_offset + safe_limit]

    def get_submitted_document(
        self, submitted_id: str, *, shop_domain: str | None = None
    ) -> dict[str, Any] | None:
        normalized_shop_domain = _normalize_shop_domain(shop_domain)
        client = self._get_supabase_client()
        if client:
            try:
                query = (
                    client.table("submitted_documents")
                    .select("*")
                    .eq("submitted_id", submitted_id)
                )
                if normalized_shop_domain:
                    query = query.eq("shop_domain", normalized_shop_domain)
                res = query.limit(1).execute()
                rows = res.data or []
                if rows:
                    row = rows[0]
                    if _matches_shop_domain(row, normalized_shop_domain):
                        return row
            except Exception as exc:
                if normalized_shop_domain and _is_missing_column_error(
                    exc, table="submitted_documents", column="shop_domain"
                ):
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
                            row = rows[0]
                            if _matches_shop_domain(row, normalized_shop_domain):
                                return row
                    except Exception:
                        LOG.exception(
                            "Failed fetching submitted document %s", submitted_id
                        )
                else:
                    LOG.exception("Failed fetching submitted document %s", submitted_id)
        document = self.submitted_documents.get(submitted_id)
        if document and not _matches_shop_domain(document, normalized_shop_domain):
            return None
        return document

    def delete_submitted_document(
        self, submitted_id: str, *, shop_domain: str | None = None
    ) -> bool:
        deleted = False
        normalized_shop_domain = _normalize_shop_domain(shop_domain)
        if normalized_shop_domain and not self.get_submitted_document(
            submitted_id,
            shop_domain=normalized_shop_domain,
        ):
            return False
        client = self._get_supabase_client()
        if client:
            try:
                query = (
                    client.table("submitted_documents")
                    .delete()
                    .eq("submitted_id", submitted_id)
                )
                if normalized_shop_domain:
                    query = query.eq("shop_domain", normalized_shop_domain)
                res = query.execute()
                deleted_rows = [
                    row
                    for row in (res.data or [])
                    if isinstance(row, dict)
                    and _matches_shop_domain(row, normalized_shop_domain)
                ]
                deleted = bool(deleted_rows)
            except Exception as exc:
                if normalized_shop_domain and _is_missing_column_error(
                    exc, table="submitted_documents", column="shop_domain"
                ):
                    try:
                        res = (
                            client.table("submitted_documents")
                            .delete()
                            .eq("submitted_id", submitted_id)
                            .execute()
                        )
                        deleted_rows = [
                            row
                            for row in (res.data or [])
                            if isinstance(row, dict)
                            and _matches_shop_domain(row, normalized_shop_domain)
                        ]
                        deleted = bool(deleted_rows)
                    except Exception:
                        LOG.exception(
                            "Failed deleting submitted document %s", submitted_id
                        )
                else:
                    LOG.exception("Failed deleting submitted document %s", submitted_id)
        if submitted_id in self.submitted_documents and (
            _matches_shop_domain(
                self.submitted_documents[submitted_id],
                normalized_shop_domain,
            )
        ):
            del self.submitted_documents[submitted_id]
            deleted = True
        return deleted
