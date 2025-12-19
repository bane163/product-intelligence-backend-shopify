"""Routes package for API routers.

This file intentionally left minimal to make `routes` a proper package.
"""

from . import agents, maf, shopify_products

__all__ = ["agents", "maf", "shopify_products"]
