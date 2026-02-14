from pydantic import BaseModel
from typing import Optional, List


class ProductCreate(BaseModel):
    title: str
    body_html: Optional[str] = ""
    vendor: Optional[str] = None


class ProductUpdate(BaseModel):
    title: Optional[str] = None
    body_html: Optional[str] = None


# Expanded Shopify product input models to match the CSV template fields
class ProductOption(BaseModel):
    name: str
    position: Optional[int] = None
    # For creation, option values are usually supplied as a list of strings
    values: Optional[List[str]] = None


class ProductVariant(BaseModel):
    # Option values (up to 3)
    option1: Optional[str] = None
    option2: Optional[str] = None
    option3: Optional[str] = None

    # Variant identifiers and pricing
    sku: Optional[str] = None
    grams: Optional[int] = None
    inventory_tracker: Optional[str] = None
    inventory_quantity: Optional[int] = None
    inventory_policy: Optional[str] = None
    fulfillment_service: Optional[str] = None
    price: Optional[float] = None
    compare_at_price: Optional[float] = None

    # Shipping / tax
    requires_shipping: Optional[bool] = None
    taxable: Optional[bool] = None
    barcode: Optional[str] = None

    # Images / weights
    variant_image: Optional[str] = None
    variant_weight_unit: Optional[str] = None
    variant_tax_code: Optional[str] = None

    # Cost / international pricing
    cost_per_item: Optional[float] = None
    price_international: Optional[str] = None
    compare_at_price_international: Optional[str] = None

    # Misc
    title: Optional[str] = None
    status: Optional[str] = None


class ProductImage(BaseModel):
    # URL to an image accessible by Shopify
    src: Optional[str] = None
    alt: Optional[str] = None
    position: Optional[int] = None


class ProductInput(BaseModel):
    """Model aligning with product_template.csv columns.

    Note: Many fields are optional; the agent should populate as much as it can.
    """

    # Core
    handle: Optional[str] = None
    title: str
    body_html: Optional[str] = None
    vendor: Optional[str] = None
    product_category: Optional[str] = None
    product_type: Optional[str] = None
    tags: Optional[str] = None
    published: Optional[bool] = None

    # Options / variants / images
    options: Optional[List[ProductOption]] = None
    variants: Optional[List[ProductVariant]] = None
    images: Optional[List[ProductImage]] = None

    # Product-level image and metadata fields
    image_src: Optional[str] = None
    image_position: Optional[int] = None
    image_alt_text: Optional[str] = None
    gift_card: Optional[bool] = None

    # SEO
    seo_title: Optional[str] = None
    seo_description: Optional[str] = None

    # Google Shopping fields
    google_shopping_category: Optional[str] = None
    google_shopping_gender: Optional[str] = None
    google_shopping_age_group: Optional[str] = None
    google_shopping_mpn: Optional[str] = None
    google_adwords_grouping: Optional[str] = None
    google_adwords_labels: Optional[str] = None
    google_shopping_condition: Optional[str] = None
    google_shopping_custom_product: Optional[bool] = None
    # custom labels 0-4
    google_custom_label_0: Optional[str] = None
    google_custom_label_1: Optional[str] = None
    google_custom_label_2: Optional[str] = None
    google_custom_label_3: Optional[str] = None
    google_custom_label_4: Optional[str] = None

    status: Optional[str] = None


class ProductsList(BaseModel):
    """Wrapper model for a list of products."""

    products: List[ProductInput]


__all__ = [
    "ProductCreate",
    "ProductUpdate",
    "ProductOption",
    "ProductVariant",
    "ProductImage",
    "ProductInput",
    "ProductsList",
]
