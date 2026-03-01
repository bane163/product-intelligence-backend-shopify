import shopify as _shopify
from typing import Any, Dict, List, Optional


class ShopifyAdapter:
    """Adapter implementing the ShopifyPort using the existing shopify.ShopifyClient."""

    def __init__(self, shop: str | None = None, token: str | None = None) -> None:
        self._client = _shopify.ShopifyClient(shop=shop, token=token)

    async def get_product(self, gid: str) -> Dict[str, Any]:
        return await self._client.get_product(gid)

    async def get_product_metafields(
        self, gid: str, identifiers: list[Dict[str, str]]
    ) -> list[Dict[str, Any]]:
        return await self._client.get_product_metafields(gid, identifiers)

    async def find_product_id_by_handle(self, handle: str) -> Optional[str]:
        return await self._client.find_product_id_by_handle(handle)

    async def find_product_id_by_sku(self, sku: str) -> Optional[str]:
        return await self._client.find_product_id_by_sku(sku)

    async def create_product_from_input(self, product: Dict[str, Any]) -> Dict[str, Any]:
        return await self._client.create_product_from_input(product)

    async def update_product_from_input(self, product: Dict[str, Any]) -> Dict[str, Any]:
        return await self._client.update_product_from_input(product)

    async def create_product_options(
        self, product_id: str, options: list[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return await self._client.create_product_options(product_id, options)

    async def bulk_create_product_variants(
        self, product_id: str, variants: list[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return await self._client.bulk_create_product_variants(product_id, variants)

    async def bulk_delete_product_variants(
        self, product_id: str, variant_ids: list[str]
    ) -> Dict[str, Any]:
        return await self._client.bulk_delete_product_variants(product_id, variant_ids)

    async def list_products_for_audit(
        self, query: str | None = None, limit: int = 50
    ) -> list[Dict[str, Any]]:
        return await self._client.list_products_for_audit(query=query, limit=limit)

    async def create_staged_upload(self) -> Dict[str, Any]:
        return await self._client.create_staged_upload()

    async def upload_to_staged_url(
        self, url: str, parameters: List[Dict[str, str]], jsonl_data: str
    ) -> None:
        return await self._client.upload_to_staged_url(url, parameters, jsonl_data)

    async def run_bulk_mutation(self, staged_upload_path: str) -> Dict[str, Any]:
        return await self._client.run_bulk_mutation(staged_upload_path)

    async def get_bulk_operation(self, operation_id: str) -> Dict[str, Any]:
        return await self._client.get_bulk_operation(operation_id)

    async def wait_for_bulk_operation(
        self,
        operation_id: str,
        *,
        poll_interval: float = 5.0,
        timeout: float = 600.0,
    ) -> Dict[str, Any]:
        return await self._client.wait_for_bulk_operation(
            operation_id, poll_interval=poll_interval, timeout=timeout
        )

    def build_product_set_jsonl(self, products: List[Dict[str, Any]]) -> str:
        return _shopify.ShopifyClient.build_product_set_jsonl(products)
