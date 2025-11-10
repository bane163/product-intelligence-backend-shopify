"""Routes package for API routers.

This file intentionally left minimal to make `routes` a proper package.
"""

from . import maf, shopify_products

__all__ = ["shopify_products", "maf"]
