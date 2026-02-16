import shopify as _shopify
from typing import Any, Dict, Optional


class ShopifyAdapter:
    """Adapter implementing the ShopifyPort using the existing shopify.ShopifyClient."""

    def __init__(self, shop: str | None = None, token: str | None = None) -> None:
        self._client = _shopify.ShopifyClient(shop=shop, token=token)

    async def find_product_id_by_handle(self, handle: str) -> Optional[str]:
        return await self._client.find_product_id_by_handle(handle)

    async def find_product_id_by_sku(self, sku: str) -> Optional[str]:
        return await self._client.find_product_id_by_sku(sku)

    async def create_product_from_input(self, product: Dict[str, Any]) -> Dict[str, Any]:
        return await self._client.create_product_from_input(product)

    async def update_product_from_input(self, product: Dict[str, Any]) -> Dict[str, Any]:
        return await self._client.update_product_from_input(product)
