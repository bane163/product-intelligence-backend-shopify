from pydantic import BaseModel
from typing import Optional


class ProductCreate(BaseModel):
    title: str
    body_html: Optional[str] = ""
    vendor: Optional[str] = None


class ProductUpdate(BaseModel):
    title: Optional[str] = None
    body_html: Optional[str] = None

__all__ = ["ProductCreate", "ProductUpdate"]
