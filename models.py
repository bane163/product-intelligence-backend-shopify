from pydantic import BaseModel
from typing import Optional, List


class ProductCreate(BaseModel):
    title: str
    body_html: Optional[str] = ""
    vendor: Optional[str] = None


class ProductUpdate(BaseModel):
    title: Optional[str] = None
    body_html: Optional[str] = None


# Expanded Shopify product input models to match typical ProductInput shape
class ProductOption(BaseModel):
    name: str
    position: Optional[int] = None
    # For creation, option values are usually supplied as a list of strings
    values: Optional[List[str]] = None


class ProductVariant(BaseModel):
    title: Optional[str] = None
    sku: Optional[str] = None
    price: Optional[float] = None
    inventory_quantity: Optional[int] = None
    # Option values for this variant (up to 3 in Shopify)
    option1: Optional[str] = None
    option2: Optional[str] = None
    option3: Optional[str] = None


class ProductImage(BaseModel):
    # URL to an image accessible by Shopify, or base64 data depending on workflow
    src: str
    alt: Optional[str] = None


class ProductInput(BaseModel):
    """Pydantic model roughly matching Shopify's ProductInput used by productCreate.

    Fields included are the most commonly-used ones for automated imports. The
    agent should populate these fields when producing product creation payloads.
    """

    title: str
    body_html: Optional[str] = None
    vendor: Optional[str] = None
    options: Optional[List[ProductOption]] = None
    variants: Optional[List[ProductVariant]] = None
    images: Optional[List[ProductImage]] = None


class ProductsList(BaseModel):
    """Wrapper model for a list of products. Use this as an agent response_format
    so the LLM returns a JSON object with a 'products' array matching ProductInput.
    """

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
