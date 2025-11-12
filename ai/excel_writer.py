import json
import os
from typing import Iterable, cast

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from .models import ProductInput, ProductsList


def _product_to_row(product: ProductInput) -> list[str]:
    """Serialize a ProductInput into a flat row for the worksheet."""
    options = (
        [option.model_dump(exclude_none=True) for option in product.options or []]
        if product.options
        else []
    )
    variants = (
        [variant.model_dump(exclude_none=True) for variant in product.variants or []]
        if product.variants
        else []
    )
    images = (
        [image.model_dump(exclude_none=True) for image in product.images or []]
        if product.images
        else []
    )

    # Convert nested structures to JSON for readability inside the cell.
    return [
        product.title,
        product.body_html or "",
        product.vendor or "",
        json.dumps(options, ensure_ascii=False) if options else "",
        json.dumps(variants, ensure_ascii=False) if variants else "",
        json.dumps(images, ensure_ascii=False) if images else "",
    ]


def _append_products(worksheet, products: Iterable[ProductInput]) -> None:
    for product in products:
        worksheet.append(_product_to_row(product))


def create_excel_workbook(products_list: ProductsList, output_path: str) -> str:
    """Create an Excel workbook describing the given products.

    Args:
        products_list: The products to write into the workbook.
        output_path: Target path for the workbook. Directories are created as needed.

    Returns:
        Absolute path to the saved workbook.
    """
    if not output_path:
        raise ValueError("output_path must be provided")

    absolute_path = os.path.abspath(output_path)
    target_dir = os.path.dirname(absolute_path)
    if target_dir and not os.path.exists(target_dir):
        os.makedirs(target_dir, exist_ok=True)

    workbook = Workbook()
    worksheet = cast(Worksheet, workbook.active)
    worksheet.title = "Products"

    headers = ["title", "body_html", "vendor", "options", "variants", "images"]
    worksheet.append(headers)

    _append_products(worksheet, products_list.products)

    workbook.save(absolute_path)
    return absolute_path
