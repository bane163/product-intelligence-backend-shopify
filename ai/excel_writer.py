import json
import os
from typing import Iterable, cast

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet
import csv
from typing import Iterable

from .models import ProductInput, ProductsList


# Shopify CSV template headers (taken from provided product_template.csv)
TEMPLATE_HEADERS = [
    "Handle",
    "Title",
    "Body (HTML)",
    "Vendor",
    "Product Category",
    "Type",
    "Tags",
    "Published",
    "Option1 Name",
    "Option1 Value",
    "Option2 Name",
    "Option2 Value",
    "Option3 Name",
    "Option3 Value",
    "Variant SKU",
    "Variant Grams",
    "Variant Inventory Tracker",
    "Variant Inventory Qty",
    "Variant Inventory Policy",
    "Variant Fulfillment Service",
    "Variant Price",
    "Variant Compare At Price",
    "Variant Requires Shipping",
    "Variant Taxable",
    "Variant Barcode",
    "Image Src",
    "Image Position",
    "Image Alt Text",
    "Gift Card",
    "SEO Title",
    "SEO Description",
    "Google Shopping / Google Product Category",
    "Google Shopping / Gender",
    "Google Shopping / Age Group",
    "Google Shopping / MPN",
    "Google Shopping / AdWords Grouping",
    "Google Shopping / AdWords Labels",
    "Google Shopping / Condition",
    "Google Shopping / Custom Product",
    "Google Shopping / Custom Label 0",
    "Google Shopping / Custom Label 1",
    "Google Shopping / Custom Label 2",
    "Google Shopping / Custom Label 3",
    "Google Shopping / Custom Label 4",
    "Variant Image",
    "Variant Weight Unit",
    "Variant Tax Code",
    "Cost per item",
    "Price / International",
    "Compare At Price / International",
    "Status",
]


def _slugify_handle(title: str) -> str:
    # simple slug: lowercase, replace spaces with dashes, remove non-url chars
    import re

    s = (title or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    return s


def _product_to_rows(product: ProductInput) -> list[list[str]]:
    """Serialize a ProductInput into one or more CSV rows following Shopify format.

    For products with multiple variants, the first row contains product-level
    information and the first variant; subsequent rows contain only Handle and
    variant-specific fields as per Shopify import CSV conventions.
    """
    rows: list[list[str]] = []

    handle = _slugify_handle(product.title)
    title = product.title
    body = product.body_html or ""
    vendor = product.vendor or ""
    published = "TRUE"

    options = product.options or []
    opt1_name = options[0].name if len(options) >= 1 else "Option1"
    opt2_name = options[1].name if len(options) >= 2 else ""
    opt3_name = options[2].name if len(options) >= 3 else ""

    images = product.images or []
    first_image = images[0].src if images else ""
    first_image_alt = images[0].alt if images and images[0].alt else ""

    variants = product.variants or []
    if not variants:
        # Create a single default variant row
        row = ["" for _ in TEMPLATE_HEADERS]
        row[TEMPLATE_HEADERS.index("Handle")] = handle
        row[TEMPLATE_HEADERS.index("Title")] = title
        row[TEMPLATE_HEADERS.index("Body (HTML)")] = body
        row[TEMPLATE_HEADERS.index("Vendor")] = vendor
        row[TEMPLATE_HEADERS.index("Published")] = published
        row[TEMPLATE_HEADERS.index("Option1 Name")] = opt1_name
        row[TEMPLATE_HEADERS.index("Option1 Value")] = "Default"
        row[TEMPLATE_HEADERS.index("Variant SKU")] = ""
        row[TEMPLATE_HEADERS.index("Variant Price")] = ""
        row[TEMPLATE_HEADERS.index("Image Src")] = first_image
        row[TEMPLATE_HEADERS.index("Image Alt Text")] = first_image_alt
        row[TEMPLATE_HEADERS.index("Variant Weight Unit")] = "g"
        row[TEMPLATE_HEADERS.index("Status")] = "active"
        rows.append(row)
        return rows

    # For products with variants, write first row with product-level and first variant
    for idx, variant in enumerate(variants):
        row = ["" for _ in TEMPLATE_HEADERS]
        row[TEMPLATE_HEADERS.index("Handle")] = handle
        if idx == 0:
            row[TEMPLATE_HEADERS.index("Title")] = title
            row[TEMPLATE_HEADERS.index("Body (HTML)")] = body
            row[TEMPLATE_HEADERS.index("Vendor")] = vendor
            row[TEMPLATE_HEADERS.index("Published")] = published
            row[TEMPLATE_HEADERS.index("Option1 Name")] = opt1_name
            if opt2_name:
                row[TEMPLATE_HEADERS.index("Option2 Name")] = opt2_name
            if opt3_name:
                row[TEMPLATE_HEADERS.index("Option3 Name")] = opt3_name
            row[TEMPLATE_HEADERS.index("Image Src")] = first_image
            row[TEMPLATE_HEADERS.index("Image Alt Text")] = first_image_alt
            row[TEMPLATE_HEADERS.index("Status")] = "active"

        # Variant-specific fields
        if variant.sku:
            row[TEMPLATE_HEADERS.index("Variant SKU")] = variant.sku
        if variant.price is not None:
            row[TEMPLATE_HEADERS.index("Variant Price")] = str(variant.price)
        if variant.inventory_quantity is not None:
            row[TEMPLATE_HEADERS.index("Variant Inventory Qty")] = str(
                variant.inventory_quantity
            )
        # Option values
        if variant.option1:
            row[TEMPLATE_HEADERS.index("Option1 Value")] = variant.option1
        if variant.option2:
            row[TEMPLATE_HEADERS.index("Option2 Value")] = variant.option2
        if variant.option3:
            row[TEMPLATE_HEADERS.index("Option3 Value")] = variant.option3

        # Defaults
        row[TEMPLATE_HEADERS.index("Variant Grams")] = ""
        row[TEMPLATE_HEADERS.index("Variant Inventory Tracker")] = ""
        row[TEMPLATE_HEADERS.index("Variant Inventory Policy")] = "deny"
        row[TEMPLATE_HEADERS.index("Variant Fulfillment Service")] = "manual"
        row[TEMPLATE_HEADERS.index("Variant Requires Shipping")] = "TRUE"
        row[TEMPLATE_HEADERS.index("Variant Taxable")] = "TRUE"
        row[TEMPLATE_HEADERS.index("Variant Weight Unit")] = "g"

        rows.append(row)

    return rows


def create_excel_workbook(products_list: ProductsList, output_path: str) -> str:
    """Create a CSV workbook (Shopify import format) describing the given products.

    The function writes a CSV file with headers matching the provided template so
    the output can be directly imported into Shopify or previewed in Collabora.
    """
    if not output_path:
        raise ValueError("output_path must be provided")

    absolute_path = os.path.abspath(output_path)
    target_dir = os.path.dirname(absolute_path)
    if target_dir and not os.path.exists(target_dir):
        os.makedirs(target_dir, exist_ok=True)

    # If output_path ends with .xlsx we still write a CSV to maintain template
    # fidelity, but keep the provided suffix so callers get the expected path.
    csv_path = (
        absolute_path if absolute_path.lower().endswith(".csv") else absolute_path + ".csv"
    )

    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(TEMPLATE_HEADERS)
        for product in products_list.products:
            rows = _product_to_rows(product)
            for r in rows:
                writer.writerow(r)

    return csv_path


def create_csv_bytes(products_list: ProductsList) -> bytes:
    """Return CSV bytes for the given ProductsList without writing to disk."""
    import io
    import csv as _csv

    output = io.StringIO()
    writer = _csv.writer(output)
    writer.writerow(TEMPLATE_HEADERS)
    for product in products_list.products:
        rows = _product_to_rows(product)
        for r in rows:
            writer.writerow(r)

    return output.getvalue().encode("utf-8")
