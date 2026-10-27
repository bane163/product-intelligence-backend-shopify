"""Canonical Catalog Health fields exposed with authenticated live Shopify values."""
from typing import Any, Literal, TypedDict

CatalogFieldId = Literal[
    'title', 'body_html', 'vendor', 'product_type', 'seo_title',
    'seo_description', 'tags', 'metafields', 'variant_operations',
]


class CatalogCurrentField(TypedDict):
    field: CatalogFieldId
    value: Any
