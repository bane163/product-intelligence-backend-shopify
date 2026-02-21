import os
from typing import Any, Dict, Optional, List
import pathlib
from urllib.parse import urlparse

import httpx

import token_store
from dotenv import load_dotenv

load_dotenv()

# Path to this module; graphql files are stored in the `graphql/` sibling folder
ROOT = pathlib.Path(__file__).parent


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
            f"https://{self.shop}/admin/api/2025-10/graphql.json"
            if self.shop
            else None
        )

    async def graphql(
        self, query: str, variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        # Ensure the underlying http client exists and we have a token/shop
        await self._ensure_client()
        payload = {"query": query, "variables": variables or {}}
        # mypy: _client is Optional but _ensure_client() ensures it's set
        assert self._client is not None
        assert self.url is not None
        resp = await self._client.post(self.url, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def _ensure_client(self) -> None:
        """Create the httpx.AsyncClient if not already created.

        This resolves the token (from provided value, env, or token_store)
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
                    f"https://{self.shop}/admin/api/2025-10/graphql.json"
                )

        if not self.shop:
            raise RuntimeError(
                "SHOPIFY_STORE must be set (either pass `shop=` or set SHOPIFY_STORE env)"
            )

        # Resolve token: explicit, env, or token_store
        if not self._token:
            self._token = os.getenv("SHOPIFY_ACCESS_TOKEN")
        if not self._token:
            self._token = token_store.get_token(self.shop)

        if not self._token:
            raise RuntimeError(
                "No access token available: set SHOPIFY_ACCESS_TOKEN, pass `token=` to ShopifyClient, "
                "or complete the OAuth flow which saves a token in the token store."
            )

        headers = {
            "X-Shopify-Access-Token": self._token,
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(headers=headers, timeout=30.0)

    def set_token(self, token: str, persist: bool = False) -> None:
        """Attach a token to the client at runtime.

        If `persist` is True the token will also be saved to `token_store` for
        future runs.
        """
        self._token = token
        if persist and self.shop:
            token_store.save_token(self.shop, token)
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
        if tags is None:
            return None
        if isinstance(tags, list):
            normalized = [str(tag).strip() for tag in tags if str(tag).strip()]
            if normalized:
                return normalized
            return [] if allow_empty else None
        if isinstance(tags, str):
            normalized = [part.strip() for part in tags.split(",") if part.strip()]
            if normalized:
                return normalized
            return [] if allow_empty else None
        return None

    @staticmethod
    def _build_product_payload(product: Dict[str, Any], *, include_id: bool) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        clearable_string_fields = {"vendor", "body_html", "product_type"}
        mapping = {
            "id": "id",
            "title": "title",
            "handle": "handle",
            "body_html": "descriptionHtml",
            "vendor": "vendor",
            "product_type": "productType",
            "status": "status",
        }
        for source_key, target_key in mapping.items():
            if source_key == "id" and not include_id:
                continue
            if source_key not in product:
                continue
            value = product.get(source_key)
            if value is None:
                continue
            if value == "" and source_key not in clearable_string_fields:
                continue
            payload[target_key] = value
        tags = ShopifyClient._normalize_tags(
            product.get("tags"),
            allow_empty="tags" in product,
        )
        if tags is not None:
            payload["tags"] = tags
        if "productType" not in payload and "product_category" in product:
            fallback_type = product.get("product_category")
            if fallback_type is not None:
                payload["productType"] = fallback_type
        seo_title = product.get("seo_title")
        seo_description = product.get("seo_description")
        if (
            "seo_title" in product
            or "seo_description" in product
            or seo_title not in (None, "")
            or seo_description not in (None, "")
        ):
            payload["seo"] = {
                "title": str(seo_title or ""),
                "description": str(seo_description or ""),
            }
        return payload

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

    async def create_product_from_input(self, product: Dict[str, Any]) -> Dict[str, Any]:
        mutation = _load_graphql("productCreate.graphql")
        product_payload = self._build_product_payload(product, include_id=False)
        response = await self.graphql(mutation, {"product": product_payload})
        created = response.get("data", {}).get("productCreate", {}).get("product", {})
        owner_id = created.get("id") if isinstance(created, dict) else None
        if isinstance(owner_id, str) and owner_id:
            metafields = self._extract_metafields_inputs(product, owner_id)
            if metafields:
                await self.set_product_metafields(owner_id, metafields)
        return response

    async def get_product(self, gid: str) -> Dict[str, Any]:
        query = _load_graphql("productQuery.graphql")
        return await self.graphql(query, {"id": gid})

    async def get_product_metafields(
        self, gid: str, identifiers: List[Dict[str, str]]
    ) -> List[Dict[str, Any]]:
        if not identifiers:
            return []
        normalized_identifiers = [
            {"namespace": str(item.get("namespace") or "").strip(), "key": str(item.get("key") or "").strip()}
            for item in identifiers
            if isinstance(item, dict)
            and str(item.get("namespace") or "").strip()
            and str(item.get("key") or "").strip()
        ]
        if not normalized_identifiers:
            return []
        query = _load_graphql("productMetafields.graphql")
        resp = await self.graphql(query, {"id": gid, "identifiers": normalized_identifiers})
        node = resp.get("data", {}).get("node", {}) if isinstance(resp, dict) else {}
        metafields = node.get("metafields") if isinstance(node, dict) else None
        if not isinstance(metafields, list):
            return []
        return [item for item in metafields if isinstance(item, dict)]

    async def create_product_options(
        self, product_id: str, options: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not options:
            return {"data": {"productOptionsCreate": {"product": None, "userErrors": []}}}
        mutation = _load_graphql("productOptionsCreate.graphql")
        normalized_options: List[Dict[str, Any]] = []
        for option in options:
            if not isinstance(option, dict):
                continue
            name = str(option.get("name") or "").strip()
            raw_values = option.get("values")
            values = [str(item).strip() for item in raw_values if str(item).strip()] if isinstance(raw_values, list) else []
            if not name or not values:
                continue
            normalized_options.append({"name": name, "values": [{"name": value} for value in values]})
        if not normalized_options:
            return {"data": {"productOptionsCreate": {"product": None, "userErrors": []}}}
        return await self.graphql(mutation, {"productId": product_id, "options": normalized_options})

    async def bulk_create_product_variants(
        self, product_id: str, variants: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not variants:
            return {"data": {"productVariantsBulkCreate": {"productVariants": [], "userErrors": []}}}
        mutation = _load_graphql("productVariantsBulkCreate.graphql")
        normalized_variants: List[Dict[str, Any]] = []
        for item in variants:
            if not isinstance(item, dict):
                continue
            option_values = item.get("option_values")
            if not isinstance(option_values, list):
                option_values = []
            normalized_option_values: List[Dict[str, str]] = []
            for option in option_values:
                if not isinstance(option, dict):
                    continue
                option_name = str(option.get("option_name") or option.get("optionName") or "").strip()
                name = str(option.get("name") or option.get("value") or "").strip()
                if option_name and name:
                    normalized_option_values.append({"optionName": option_name, "name": name})
            if not normalized_option_values:
                continue
            payload: Dict[str, Any] = {"optionValues": normalized_option_values}
            sku = str(item.get("sku") or "").strip()
            if sku:
                payload["sku"] = sku
            price = item.get("price")
            if price not in (None, ""):
                payload["price"] = str(price)
            inventory_quantity = item.get("inventory_quantity")
            if isinstance(inventory_quantity, int):
                payload["inventoryQuantities"] = [{"availableQuantity": inventory_quantity}]
            normalized_variants.append(payload)
        if not normalized_variants:
            return {"data": {"productVariantsBulkCreate": {"productVariants": [], "userErrors": []}}}
        return await self.graphql(mutation, {"productId": product_id, "variants": normalized_variants})

    async def bulk_delete_product_variants(
        self, product_id: str, variant_ids: List[str]
    ) -> Dict[str, Any]:
        normalized_ids = [str(item).strip() for item in variant_ids if str(item).strip()]
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
        mutation = _load_graphql("productUpdate.graphql")
        product_payload = self._build_product_payload(product, include_id=True)
        response = await self.graphql(mutation, {"product": product_payload})
        updated = response.get("data", {}).get("productUpdate", {}).get("product", {})
        owner_id = updated.get("id") if isinstance(updated, dict) else None
        if isinstance(owner_id, str) and owner_id:
            metafields = self._extract_metafields_inputs(product, owner_id)
            if metafields:
                await self.set_product_metafields(owner_id, metafields)
        return response

    async def find_product_id_by_handle(self, handle: str) -> str | None:
        query = _load_graphql("productByHandle.graphql")
        resp = await self.graphql(query, {"query": f"handle:{handle}"})
        nodes = resp.get("data", {}).get("products", {}).get("nodes", [])
        if not nodes:
            return None
        if len(nodes) > 1:
            raise RuntimeError(f"Multiple products matched handle '{handle}'")
        return nodes[0].get("id")

    async def find_product_id_by_sku(self, sku: str) -> str | None:
        query = _load_graphql("productByHandle.graphql")
        resp = await self.graphql(query, {"query": f"sku:{sku}"})
        nodes = resp.get("data", {}).get("products", {}).get("nodes", [])
        if not nodes:
            return None
        if len(nodes) > 1:
            raise RuntimeError(f"Multiple products matched SKU '{sku}'")
        return nodes[0].get("id")

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
