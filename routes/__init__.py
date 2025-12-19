"""Routes package for API routers.

This file intentionally left minimal to make `routes` a proper package. We
also expose the `files` and `wopi` modules for convenience.
"""

from . import agents, maf, shopify_products, files, wopi

__all__ = ["agents", "files", "wopi", "maf", "shopify_products"]
