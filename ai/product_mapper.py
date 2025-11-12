from typing import Any, Dict, List
from shopify_supabase_backend.ai.models import ProductInput, ProductOption, ProductsList


def productinput_to_create_args(product: ProductInput) -> Dict[str, Any]:
    """Map a ProductInput instance to the arguments accepted by
    ShopifyClient.create_product.

    Returns a dict with keys: title, body_html, vendor, product_options
    where product_options follows the API shape expected by the client.
    """
    title = product.title
    body_html = product.body_html or ""
    vendor = product.vendor

    product_options: List[Dict[str, Any]] | None = None
    if product.options:
        po: List[Dict[str, Any]] = []
        for opt in product.options:
            # opt.values is a list of strings; convert to API shape
            values = [{"name": str(v)} for v in (opt.values or [])]
            po.append({"name": opt.name, "values": values})
        product_options = po

    return {
        "title": title,
        "body_html": body_html,
        "vendor": vendor,
        "product_options": product_options,
    }


async def create_products_from_productslist(
    client: Any, products_list: ProductsList
) -> List[Dict[str, Any]]:
    """Given a ShopifyClient-like object and a ProductsList, call
    client.create_product for each product and return the list of responses.

    This keeps a small, testable wrapper around the existing client method so
    calling code can convert agent outputs into Shopify API calls.
    """
    results: List[Dict[str, Any]] = []
    for p in products_list.products:
        args = productinput_to_create_args(p)
        # client.create_product signature: (title, body_html='', vendor=None, product_options=None)
        resp = await client.create_product(
            args["title"], args["body_html"], args["vendor"], args["product_options"]
        )
        results.append(resp)
    return results
