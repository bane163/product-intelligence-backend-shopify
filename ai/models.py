from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Any, Optional, List, Literal


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


class ProductMetafield(BaseModel):
    namespace: str
    key: str
    value: str
    type: Optional[str] = None


class ProductSourceRef(BaseModel):
    source_file_id: Optional[str] = None
    anchor_id: Optional[str] = None
    field: Optional[str] = None
    document_kind: Optional[str] = None
    sheet: Optional[str] = None
    cell: Optional[str] = None
    cell_range: Optional[str] = None
    page: Optional[int] = None
    bbox: Optional[List[float]] = None
    value: Optional[str] = None


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
    tags: Optional[str | List[str]] = None
    published: Optional[bool] = None

    # Options / variants / images
    options: Optional[List[ProductOption]] = None
    variants: Optional[List[ProductVariant]] = None
    images: Optional[List[ProductImage]] = None
    metafields: Optional[List[ProductMetafield]] = None
    source_refs: Optional[List[ProductSourceRef]] = None

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


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_has_meaningful_value(item) for item in value)
    if isinstance(value, dict):
        return any(_has_meaningful_value(item) for item in value.values())
    return True


class StrictSchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


SuggestionScalar = str | int | float | bool
SuggestionValue = SuggestionScalar | list[SuggestionScalar] | list[list[SuggestionScalar]]


class ProductIntelligenceMetafieldPatch(StrictSchemaModel):
    namespace: str = Field(min_length=1)
    key: str = Field(min_length=1)
    value: str = Field(min_length=1)
    type: Optional[str] = None


class ProductIntelligenceVariantOptionValuePatch(StrictSchemaModel):
    option_name: str = Field(min_length=1)
    name: str = Field(min_length=1)


class ProductIntelligenceVariantCreateOptionPatch(StrictSchemaModel):
    name: str = Field(min_length=1)
    values: List[str] = Field(min_length=1)

    @field_validator("values")
    @classmethod
    def validate_values(cls, value: List[str]) -> List[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized:
            raise ValueError("values must include at least one non-empty item")
        return normalized


class ProductIntelligenceVariantCreatePatch(StrictSchemaModel):
    option_values: List[ProductIntelligenceVariantOptionValuePatch] = Field(min_length=1)
    sku: Optional[str] = None
    price: Optional[str | float | int] = None
    inventory_quantity: Optional[int] = None


class ProductIntelligenceVariantDefaultsPatch(StrictSchemaModel):
    copy_from_first_variant: Optional[bool] = None
    requires_review: Optional[bool] = None


class ProductIntelligenceVariantOperationsPatch(StrictSchemaModel):
    create_options: List[ProductIntelligenceVariantCreateOptionPatch] = Field(
        default_factory=list
    )
    create_variants: List[ProductIntelligenceVariantCreatePatch] = Field(default_factory=list)
    defaults: Optional[ProductIntelligenceVariantDefaultsPatch] = None


class ProductIntelligencePatchPayload(StrictSchemaModel):
    title: Optional[str] = None
    body_html: Optional[str] = None
    vendor: Optional[str] = None
    product_type: Optional[str] = None
    product_category: Optional[str] = None
    seo_title: Optional[str] = None
    seo_description: Optional[str] = None
    tags: Optional[str | List[str]] = None
    metafields: Optional[List[ProductIntelligenceMetafieldPatch]] = None
    variant_operations: Optional[ProductIntelligenceVariantOperationsPatch] = None

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: Optional[str | List[str]]) -> Optional[str | List[str]]:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        if isinstance(value, list):
            normalized = [item.strip() for item in value if item.strip()]
            return normalized or None
        return value

    @model_validator(mode="after")
    def validate_non_empty(self) -> "ProductIntelligencePatchPayload":
        payload = self.model_dump(exclude_none=True)
        if not _has_meaningful_value(payload):
            raise ValueError("patch_payload must include at least one non-empty field")
        return self


class ProductIntelligenceInferredDimension(StrictSchemaModel):
    dimension: Optional[str] = None
    detected_value: Optional[SuggestionValue] = None
    canonical_value: Optional[SuggestionValue] = None
    detected_values: Optional[List[str]] = None
    canonical_values: Optional[List[str]] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    source: Optional[Literal["title", "description", "image"]] = None
    evidence_sources: Optional[List[Literal["title", "description", "image"]]] = None


class ProductIntelligenceSuggestionDetails(StrictSchemaModel):
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    rule_id: Optional[str] = None
    detected_value: Optional[SuggestionValue] = None
    canonical_value: Optional[SuggestionValue] = None
    detected_values: Optional[List[str]] = None
    canonical_values: Optional[List[str]] = None
    evidence_source: Optional[Literal["title", "description", "image"]] = None
    evidence_sources: Optional[List[Literal["title", "description", "image"]]] = None
    inferred_dimensions: Optional[List[ProductIntelligenceInferredDimension]] = None


class ProductIntelligenceSuggestionDraft(StrictSchemaModel):
    product_index: int = Field(ge=0)
    category: str = Field(min_length=1)
    severity: Literal["low", "medium", "high"] = "low"
    message: str = Field(min_length=1)
    patch_payload: ProductIntelligencePatchPayload
    details: Optional[ProductIntelligenceSuggestionDetails] = None
    product_title: Optional[str] = None

    @field_validator("patch_payload")
    @classmethod
    def validate_patch_payload(
        cls, value: ProductIntelligencePatchPayload
    ) -> ProductIntelligencePatchPayload:
        if not _has_meaningful_value(value.model_dump(exclude_none=True)):
            raise ValueError("patch_payload must include at least one non-empty field")
        return value


class ProductIntelligenceSuggestionsList(StrictSchemaModel):
    suggestions: List[ProductIntelligenceSuggestionDraft]


__all__ = [
    "ProductCreate",
    "ProductUpdate",
    "ProductOption",
    "ProductVariant",
    "ProductImage",
    "ProductMetafield",
    "ProductSourceRef",
    "ProductInput",
    "ProductsList",
    "ProductIntelligenceSuggestionDraft",
    "ProductIntelligenceSuggestionsList",
]
