import logging
import os
import asyncio
import pathlib
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

import shopify_session_store
from application.domain.shopify_product_normalization import (
    build_product_payload,
    build_product_set_identifier,
    build_product_set_input,
    build_product_set_jsonl,
    normalize_product_options,
    normalize_tags,
    normalize_variant_ids,
    normalize_variant_inputs,
)
from dotenv import load_dotenv
from shared.metrics_signals import signal_shopify_retry

load_dotenv()

# Path to this module; graphql files are stored in the `graphql/` sibling folder
ROOT = pathlib.Path(__file__).parent
LOG = logging.getLogger(__name__)


def _load_graphql(name: str) -> str:
    """Load a .graphql file from the graphql/ subfolder next to this module.

    Raises RuntimeError if the file doesn't exist so callers get a clear error.
    """
    path = ROOT / "graphql" / name
    try:
        return path.read_text()
    except FileNotFoundError:
        raise RuntimeError(f"GraphQL file not found: {path}")


def _normalize_shop(shop: str | None) -> str | None:
    if not shop:
        return None
    value = shop.strip()
    if not value:
        return None
    if "://" in value:
        parsed = urlparse(value)
        host = parsed.netloc or parsed.path
        return host.strip("/") or None
    return value.strip("/")


def _should_fallback_metafields_query(error_message: str) -> bool:
    lowered = error_message.lower()
    fallback_markers = (
        "doesn't accept argument 'keys'",
        'doesn\'t accept argument "keys"',
        "does not accept argument 'keys'",
        'does not accept argument "keys"',
        'unknown argument "keys"',
        "code=argumentnotaccepted",
        "code=variablerequiresvalidtype",
        "hasmetafieldsidentifier",
    )
    return any(marker in lowered for marker in fallback_markers)


def _parse_retry_after_seconds(value: str | None) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = float(value.strip())
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _extract_throttle_message(errors: Any) -> str | None:
    if not isinstance(errors, list):
        return None
    for item in errors:
        if not isinstance(item, dict):
            continue
        extensions = item.get("extensions")
        code = (
            str(extensions.get("code") or "").strip().upper()
            if isinstance(extensions, dict)
            else ""
        )
        message = str(item.get("message") or "").strip()
        lowered = message.lower()
        if code == "THROTTLED" or "throttle" in lowered or "too many requests" in lowered:
            return message or "Shopify GraphQL throttled"
    return None


class _ShopifyThrottledError(RuntimeError):
    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ShopifyClient:
    """
    Minimal async Shopify GraphQL helper.

    Environment variables used when shop/token not provided:
      - SHOPIFY_STORE (e.g. my-store.myshopify.com)
      - SHOPIFY_ACCESS_TOKEN (Admin API access token)
    """

    def __init__(self, shop: Optional[str] = None, token: Optional[str] = None) -> None:
        # Defer resolution/creation of the httpx client until it's needed.
        # This allows constructing a ShopifyClient with only a shop or only
        # client credentials in process, and attaching the token later.
        self.shop = _normalize_shop(shop or os.getenv("SHOPIFY_STORE"))
        # token may be provided directly; otherwise resolved lazily
        self._token = token or os.getenv("SHOPIFY_ACCESS_TOKEN")

        # HTTPX async client will be created on first request once token is
        # available. Keep it None for now.
        self._client: Optional[httpx.AsyncClient] = None
        # Build URL lazily when shop known; store template now if shop present
        self.url = (
            f"https://{self.shop}/admin/api/2026-07/graphql.json"
            if self.shop
            else None
        )

    _RETRYABLE_ERRORS = (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        _ShopifyThrottledError,
    )
    _MAX_RETRIES = 3
    _BACKOFF_BASE = 1.0

    async def graphql(
        self, query: str, variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        await self._ensure_client()
        payload = {"query": query, "variables": variables or {}}
        assert self._client is not None
        assert self.url is not None

        last_exc: Exception | None = None
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                resp = await self._client.post(self.url, json=payload)
                if resp.status_code == 429:
                    request_id = (
                        resp.headers.get("x-request-id")
                        or resp.headers.get("x-request-id".title())
                    )
                    message = "Shopify GraphQL throttled (HTTP 429)"
                    if request_id:
                        message = f"{message} request_id={request_id}"
                    raise _ShopifyThrottledError(
                        message,
                        retry_after_seconds=_parse_retry_after_seconds(
                            resp.headers.get("Retry-After")
                        ),
                    )
                resp.raise_for_status()
                body = resp.json()
                top_level_errors = body.get("errors")
                if isinstance(top_level_errors, list) and top_level_errors:
                    throttle_message = _extract_throttle_message(top_level_errors)
                    if throttle_message:
                        raise _ShopifyThrottledError(throttle_message)
                    messages: list[str] = []
                    for err in top_level_errors:
                        if isinstance(err, dict):
                            msg = err.get("message")
                            code = (
                                err.get("extensions", {}).get("code")
                                if isinstance(err.get("extensions"), dict)
                                else None
                            )
                            if isinstance(msg, str) and msg.strip():
                                messages.append(
                                    f"{msg} (code={code})" if code else msg
                                )
                    if not messages:
                        messages = [str(top_level_errors)]
                    request_id = (
                        resp.headers.get("x-request-id")
                        or resp.headers.get("x-request-id".title())
                    )
                    if request_id:
                        messages.append(f"request_id={request_id}")
                    raise RuntimeError(
                        "Shopify GraphQL error: " + " | ".join(messages)
                    )
                return body
            except self._RETRYABLE_ERRORS as exc:
                last_exc = exc
                if attempt < self._MAX_RETRIES:
                    retry_after_seconds = (
                        exc.retry_after_seconds
                        if isinstance(exc, _ShopifyThrottledError)
                        else None
                    )
                    signal_shopify_retry(
                        shop=self.shop,
                        attempt=attempt,
                        max_attempts=self._MAX_RETRIES,
                        reason="throttled"
                        if isinstance(exc, _ShopifyThrottledError)
                        else "retryable_error",
                        retry_after_seconds=retry_after_seconds,
                        error=str(exc),
                    )
                    LOG.warning(
                        "Shopify GraphQL retry attempt=%s/%s shop=%s error=%s",
                        attempt,
                        self._MAX_RETRIES,
                        self.shop,
                        exc,
                    )
                    await asyncio.sleep(
                        retry_after_seconds
                        if retry_after_seconds is not None
                        else self._BACKOFF_BASE * (2 ** (attempt - 1))
                    )
        if last_exc is not None:
            LOG.error(
                "Shopify GraphQL failed after retries shop=%s url=%s error=%s",
                self.shop,
                self.url,
                last_exc,
            )
        raise last_exc  # type: ignore[misc]

    async def _ensure_client(self) -> None:
        """Create the httpx.AsyncClient if not already created.

        This resolves the token from an explicit value, environment, or the
        canonical encrypted Supabase app session.
        and ensures `self.url` is set. Raises RuntimeError if shop or token
        still missing.
        """
        if self._client is not None:
            return

        # Resolve shop
        if not self.shop:
            self.shop = os.getenv("SHOPIFY_STORE")
            self.shop = _normalize_shop(self.shop)
            if self.shop:
                self.url = (
                    f"https://{self.shop}/admin/api/2026-07/graphql.json"
                )

        if not self.shop:
            raise RuntimeError(
                "SHOPIFY_STORE must be set (either pass `shop=` or set SHOPIFY_STORE env)"
            )

        # Resolve token: explicit, env, or canonical app session.
        if not self._token:
            self._token = os.getenv("SHOPIFY_ACCESS_TOKEN")
        if not self._token:
            self._token = shopify_session_store.get_offline_access_token(self.shop)

        if not self._token:
            raise RuntimeError(
                "No access token available: set SHOPIFY_ACCESS_TOKEN, pass `token=` to ShopifyClient, "
                "or complete the OAuth flow which saves a token in the token store."
            )

        headers = {
            "X-Shopify-Access-Token": self._token,
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=30.0,
            transport=httpx.AsyncHTTPTransport(retries=3),
        )

    def set_token(self, token: str, persist: bool = False) -> None:
        """Attach a token to the client at runtime.

        Persistence is owned by Shopify's canonical app session store.
        """
        self._token = token
        if persist:
            raise RuntimeError("Shopify credentials are persisted by the canonical app session store")
        # If client already exists, update its header in-place
        if self._client is not None:
            self._client.headers["X-Shopify-Access-Token"] = token

    async def create_product(
        self,
        title: str,
        body_html: str = "",
        vendor: Optional[str] = None,
        product_options: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Create a product with optional product options.

        Accepts a minimal payload that includes title and an optional
        list of product options in the shape:
          [{"name": "Color", "values": [{"name": "Red"}, {"name": "Blue"}]}, ...]
        """
        mutation = _load_graphql("productCreate.graphql")
        product_payload: Dict[str, Any] = {"title": title}
        if body_html:
            product_payload["descriptionHtml"] = body_html
        if vendor:
            product_payload["vendor"] = vendor
        if product_options:
            # Expect product_options to be provided using the API shape:
            # [{"name": "Color", "values": [{"name": "Red"}, ...]}, ...]
            product_payload["productOptions"] = product_options

        return await self.graphql(mutation, {"product": product_payload})

    @staticmethod
    def _normalize_tags(tags: Any, *, allow_empty: bool = False) -> list[str] | None:
        return normalize_tags(tags, allow_empty=allow_empty)

    @staticmethod
    def _build_product_payload(product: Dict[str, Any], *, include_id: bool) -> Dict[str, Any]:
        return build_product_payload(product, include_id=include_id)

    @staticmethod
    def _extract_metafields_inputs(product: Dict[str, Any], owner_id: str) -> list[Dict[str, str]]:
        raw = product.get("metafields")
        if not isinstance(raw, list):
            return []
        inputs: list[Dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            namespace = item.get("namespace")
            key = item.get("key")
            value = item.get("value")
            mf_type = item.get("type") or "single_line_text_field"
            if not all(isinstance(v, str) and v.strip() for v in [namespace, key, value, mf_type]):
                continue
            inputs.append(
                {
                    "ownerId": owner_id,
                    "namespace": namespace.strip(),
                    "key": key.strip(),
                    "value": value.strip(),
                    "type": mf_type.strip(),
                }
            )
        return inputs

    async def set_product_metafields(self, owner_id: str, metafields: list[Dict[str, str]]) -> Dict[str, Any]:
        if not metafields:
            return {"data": {"metafieldsSet": {"metafields": [], "userErrors": []}}}
        mutation = _load_graphql("metafieldsSet.graphql")
        return await self.graphql(mutation, {"metafields": metafields})

    async def _run_product_mutation_from_input(
        self,
        *,
        mutation_file: str,
        response_key: str,
        product: Dict[str, Any],
        include_id: bool,
    ) -> Dict[str, Any]:
        mutation = _load_graphql(mutation_file)
        product_payload = self._build_product_payload(product, include_id=include_id)
        response = await self.graphql(mutation, {"product": product_payload})
        result_product = (
            response.get("data", {}).get(response_key, {}).get("product", {})
            if isinstance(response, dict)
            else {}
        )
        owner_id = result_product.get("id") if isinstance(result_product, dict) else None
        if isinstance(owner_id, str) and owner_id:
            metafields = self._extract_metafields_inputs(product, owner_id)
            if metafields:
                await self.set_product_metafields(owner_id, metafields)
        return response

    async def create_product_from_input(self, product: Dict[str, Any]) -> Dict[str, Any]:
        return await self._run_product_mutation_from_input(
            mutation_file="productCreate.graphql",
            response_key="productCreate",
            product=product,
            include_id=False,
        )

    async def get_product(self, gid: str) -> Dict[str, Any]:
        query = _load_graphql("productQuery.graphql")
        return await self.graphql(query, {"id": gid})

    async def get_product_metafields(
        self, gid: str, identifiers: List[Dict[str, str]]
    ) -> List[Dict[str, Any]]:
        if not identifiers:
            return []
        requested_pairs = [
            (
                str(item.get("namespace") or "").strip(),
                str(item.get("key") or "").strip(),
            )
            for item in identifiers
            if isinstance(item, dict)
        ]
        requested_pairs = [
            (namespace, key)
            for namespace, key in requested_pairs
            if namespace and key
        ]
        keys = [f"{namespace}.{key}" for namespace, key in requested_pairs]
        if not keys:
            return []
        query = _load_graphql("productMetafields.graphql")
        used_fallback_query = False
        try:
            resp = await self.graphql(query, {"id": gid, "keys": keys})
        except RuntimeError as exc:
            if not _should_fallback_metafields_query(str(exc)):
                raise
            used_fallback_query = True
            fallback_query = _load_graphql("productMetafieldsAll.graphql")
            resp = await self.graphql(fallback_query, {"id": gid})
        node = resp.get("data", {}).get("node", {}) if isinstance(resp, dict) else {}
        metafields = node.get("metafields") if isinstance(node, dict) else {}
        nodes = metafields.get("nodes") if isinstance(metafields, dict) else None
        if not isinstance(nodes, list):
            return []
        normalized_nodes = [item for item in nodes if isinstance(item, dict)]
        if not used_fallback_query:
            return normalized_nodes
        requested_set = set(requested_pairs)
        return [
            item
            for item in normalized_nodes
            if (
                str(item.get("namespace") or "").strip(),
                str(item.get("key") or "").strip(),
            )
            in requested_set
        ]

    async def create_product_options(
        self, product_id: str, options: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        normalized_options = normalize_product_options(options)
        if not normalized_options:
            return {
                "data": {"productOptionsCreate": {"product": None, "userErrors": []}}
            }
        mutation = _load_graphql("productOptionsCreate.graphql")
        return await self.graphql(
            mutation, {"productId": product_id, "options": normalized_options}
        )

    async def bulk_create_product_variants(
        self, product_id: str, variants: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        normalized_variants = normalize_variant_inputs(variants)
        if not normalized_variants:
            return {
                "data": {
                    "productVariantsBulkCreate": {
                        "productVariants": [],
                        "userErrors": [],
                    }
                }
            }
        mutation = _load_graphql("productVariantsBulkCreate.graphql")
        return await self.graphql(
            mutation, {"productId": product_id, "variants": normalized_variants}
        )

    async def bulk_delete_product_variants(
        self, product_id: str, variant_ids: List[str]
    ) -> Dict[str, Any]:
        normalized_ids = normalize_variant_ids(variant_ids)
        if not normalized_ids:
            return {"data": {"productVariantsBulkDelete": {"userErrors": []}}}
        mutation = _load_graphql("productVariantsBulkDelete.graphql")
        return await self.graphql(mutation, {"productId": product_id, "variantsIds": normalized_ids})

    async def update_product(
        self, gid: str, title: Optional[str] = None, body_html: Optional[str] = None
    ) -> Dict[str, Any]:
        mutation = _load_graphql("productUpdate.graphql")
        input_payload: Dict[str, Any] = {"id": gid}
        if title is not None:
            input_payload["title"] = title
        if body_html is not None:
            input_payload["descriptionHtml"] = body_html
        return await self.graphql(mutation, {"product": input_payload})

    async def update_product_from_input(self, product: Dict[str, Any]) -> Dict[str, Any]:
        return await self._run_product_mutation_from_input(
            mutation_file="productUpdate.graphql",
            response_key="productUpdate",
            product=product,
            include_id=True,
        )

    async def _find_product_id_by_query(
        self, *, query_text: str, entity_label: str, value: str
    ) -> str | None:
        query = _load_graphql("productByHandle.graphql")
        resp = await self.graphql(query, {"query": query_text})
        nodes = resp.get("data", {}).get("products", {}).get("nodes", [])
        if not nodes:
            return None
        if len(nodes) > 1:
            raise RuntimeError(f"Multiple products matched {entity_label} '{value}'")
        return nodes[0].get("id")

    async def find_product_id_by_handle(self, handle: str) -> str | None:
        return await self._find_product_id_by_query(
            query_text=f"handle:{handle}",
            entity_label="handle",
            value=handle,
        )

    async def find_product_id_by_sku(self, sku: str) -> str | None:
        return await self._find_product_id_by_query(
            query_text=f"sku:{sku}",
            entity_label="SKU",
            value=sku,
        )

    async def delete_product(self, gid: str) -> Dict[str, Any]:
        mutation = _load_graphql("productDelete.graphql")
        return await self.graphql(mutation, {"input": {"id": gid}})

    async def list_products_for_audit(
        self, query: Optional[str] = None, limit: int = 50
    ) -> list[Dict[str, Any]]:
        gql = _load_graphql("productsForAudit.graphql")
        remaining = max(1, min(limit, 1000))
        page_size = 50
        after: str | None = None
        collected: list[Dict[str, Any]] = []

        while remaining > 0:
            batch_size = min(page_size, remaining)
            resp = await self.graphql(
                gql,
                {
                    "first": batch_size,
                    "after": after,
                    "query": query or None,
                },
            )
            products = resp.get("data", {}).get("products", {})
            edges = products.get("edges", []) if isinstance(products, dict) else []
            for edge in edges:
                node = edge.get("node") if isinstance(edge, dict) else None
                if not isinstance(node, dict):
                    continue
                seo = node.get("seo") if isinstance(node.get("seo"), dict) else {}
                options = node.get("options") if isinstance(node.get("options"), list) else []
                images = node.get("images") if isinstance(node.get("images"), dict) else {}
                image_nodes = images.get("nodes") if isinstance(images.get("nodes"), list) else []
                variants = node.get("variants") if isinstance(node.get("variants"), dict) else {}
                variant_nodes = (
                    variants.get("nodes") if isinstance(variants.get("nodes"), list) else []
                )
                collected.append(
                    {
                        "id": node.get("id"),
                        "title": node.get("title"),
                        "handle": node.get("handle"),
                        "vendor": node.get("vendor"),
                        "product_type": node.get("productType"),
                        "status": node.get("status"),
                        "tags": node.get("tags"),
                        "body_html": node.get("descriptionHtml"),
                        "seo_title": seo.get("title"),
                        "seo_description": seo.get("description"),
                        "options": [
                            {
                                "id": option.get("id"),
                                "name": option.get("name"),
                                "position": option.get("position"),
                                "values": option.get("optionValues")
                                if isinstance(option.get("optionValues"), list)
                                else [],
                            }
                            for option in options
                            if isinstance(option, dict)
                        ],
                        "featured_image": (
                            {
                                "url": node.get("featuredImage", {}).get("url"),
                                "altText": node.get("featuredImage", {}).get("altText"),
                            }
                            if isinstance(node.get("featuredImage"), dict)
                            else None
                        ),
                        "images": [
                            {
                                "url": item.get("url"),
                                "altText": item.get("altText"),
                            }
                            for item in image_nodes
                            if isinstance(item, dict)
                        ],
                        "variants": [
                            {
                                "id": item.get("id"),
                                "title": item.get("title"),
                                "sku": item.get("sku"),
                                "price": item.get("price"),
                                "inventory_quantity": item.get("inventoryQuantity"),
                                "selectedOptions": item.get("selectedOptions")
                                if isinstance(item.get("selectedOptions"), list)
                                else [],
                            }
                            for item in variant_nodes
                            if isinstance(item, dict)
                        ],
                    }
                )
                remaining -= 1
                if remaining <= 0:
                    break

            page_info = products.get("pageInfo", {}) if isinstance(products, dict) else {}
            has_next = bool(page_info.get("hasNextPage"))
            after = page_info.get("endCursor") if has_next else None
            if not has_next or not after:
                break

        return collected

    # ── Bulk Operation helpers ──────────────────────────────────────────

    async def create_staged_upload(self) -> Dict[str, Any]:
        """Create a staged upload target for a JSONL file used in bulk mutations."""
        mutation = _load_graphql("stagedUploadsCreate.graphql")
        try:
            resp = await self.graphql(mutation, {
                "input": [{
                    "resource": "BULK_MUTATION_VARIABLES",
                    "filename": "bulk_import.jsonl",
                    "mimeType": "text/jsonl",
                    "httpMethod": "POST",
                }]
            })
        except Exception:
            LOG.exception(
                "create_staged_upload failed shop=%s url=%s",
                self.shop,
                self.url,
            )
            raise
        targets = resp.get("data", {}).get("stagedUploadsCreate", {}).get("stagedTargets", [])
        errors = resp.get("data", {}).get("stagedUploadsCreate", {}).get("userErrors", [])
        if errors:
            raise RuntimeError(f"Staged upload creation failed: {errors}")
        if not targets:
            raise RuntimeError("No staged upload targets returned")
        return targets[0]

    async def upload_to_staged_url(
        self, url: str, parameters: List[Dict[str, str]], jsonl_data: str
    ) -> None:
        """Upload JSONL data to a Shopify staged upload URL via multipart POST."""
        form_fields: Dict[str, str] = {}
        for param in parameters:
            name = param.get("name", "")
            value = param.get("value", "")
            if name:
                form_fields[name] = value
        files = {"file": ("bulk_import.jsonl", jsonl_data.encode(), "text/jsonl")}
        # Use a clean client — the Shopify client has Content-Type: application/json
        # which conflicts with the multipart/form-data the GCS upload requires.
        async with httpx.AsyncClient(timeout=60.0) as upload_client:
            resp = await upload_client.post(url, data=form_fields, files=files)
            resp.raise_for_status()

    async def run_bulk_mutation(self, staged_upload_path: str) -> Dict[str, Any]:
        """Start a bulk mutation operation using the productSet mutation."""
        product_set_mutation = _load_graphql("productSet.graphql")
        mutation = _load_graphql("bulkOperationRunMutation.graphql")
        resp = await self.graphql(mutation, {
            "mutation": product_set_mutation,
            "stagedUploadPath": staged_upload_path,
        })
        payload = resp.get("data", {}).get("bulkOperationRunMutation", {})
        errors = payload.get("userErrors", [])
        if errors:
            raise RuntimeError(f"Bulk mutation failed to start: {errors}")
        bulk_op = payload.get("bulkOperation")
        if not bulk_op:
            raise RuntimeError("No bulk operation returned")
        return bulk_op

    async def get_bulk_operation(self, operation_id: str) -> Dict[str, Any]:
        """Query the status of a bulk operation by ID."""
        query = _load_graphql("bulkOperationQuery.graphql")
        resp = await self.graphql(query)
        bulk_op = resp.get("data", {}).get("currentBulkOperation", {})
        if (
            isinstance(bulk_op, dict)
            and bulk_op.get("id")
            and operation_id
            and bulk_op.get("id") != operation_id
        ):
            raise RuntimeError(
                "Current bulk mutation operation does not match requested operation: "
                f"expected={operation_id} actual={bulk_op.get('id')}"
            )
        return bulk_op

    async def wait_for_bulk_operation(
        self,
        operation_id: str,
        *,
        poll_interval: float = 5.0,
        timeout: float = 600.0,
    ) -> Dict[str, Any]:
        """Poll a bulk operation until it reaches a terminal state."""
        elapsed = 0.0
        terminal_statuses = {"COMPLETED", "FAILED", "CANCELED", "EXPIRED"}
        while elapsed < timeout:
            status = await self.get_bulk_operation(operation_id)
            if status.get("status") in terminal_statuses:
                return status
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        raise RuntimeError(
            f"Bulk operation {operation_id} timed out after {timeout}s"
        )

    @staticmethod
    def build_product_set_jsonl(products: List[Dict[str, Any]]) -> str:
        """Build JSONL content for bulkOperationRunMutation with productSet."""
        return build_product_set_jsonl(products)

    @staticmethod
    def _build_product_set_input(product: Dict[str, Any]) -> Dict[str, Any]:
        """Build a ProductSetInput dict from internal product format."""
        return build_product_set_input(product)

    @staticmethod
    def _build_product_set_identifier(product: Dict[str, Any]) -> Dict[str, str] | None:
        """Build a ProductSetIdentifiers dict for upsert matching."""
        return build_product_set_identifier(product)
