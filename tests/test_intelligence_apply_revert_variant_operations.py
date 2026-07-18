import pytest

from application.use_cases.intelligence_apply_suggestion import execute as apply_execute
from application.use_cases.intelligence_revert_suggestion import execute as revert_execute


class _FakeSupabaseApply:
    def __init__(self) -> None:
        self.applied_previous_payload = None
        self.intelligence = self

    def get_product_intelligence_suggestion(self, suggestion_id: str, shop_domain=None):
        _ = suggestion_id, shop_domain
        return {
            "suggestion_id": "sug-1",
            "status": "pending",
            "audit_id": "audit-1",
            "product_index": 0,
            "product_title": "Catalog Product",
            "patch_payload": {
                "variant_operations": {
                    "create_options": [{"name": "Size", "values": ["S", "M"]}],
                    "create_variants": [
                        {
                            "option_values": [{"option_name": "Size", "name": "S"}],
                            "sku": "SKU-S",
                            "price": "10.00",
                        },
                        {
                            "option_values": [{"option_name": "Size", "name": "M"}],
                            "sku": "SKU-M",
                            "price": "10.00",
                        },
                    ],
                }
            },
        }

    def get_product_intelligence_audit(self, audit_id: str, shop_domain=None):
        _ = audit_id, shop_domain
        return {
            "totals": {
                "audited_products": [
                    {
                        "id": "gid://shopify/Product/1",
                        "title": "Catalog Product",
                    }
                ]
            }
        }

    def mark_product_intelligence_suggestion_applied(
        self, suggestion_id: str, previous_payload, patch_payload, shop_domain=None
    ):
        _ = suggestion_id, patch_payload, shop_domain
        self.applied_previous_payload = previous_payload
        return {"suggestion_id": "sug-1", "status": "applied"}

    def create_product_intelligence_suggestion(self, suggestion, shop_domain=None):
        _ = suggestion, shop_domain
        return None


class _FakeShopifyApply:
    def __init__(self) -> None:
        self.created_options = []
        self.created_variants = []
        self.updated_payload = None

    async def get_product(self, gid: str):
        return {
            "data": {
                "node": {
                    "id": gid,
                    "title": "Catalog Product",
                    "descriptionHtml": "",
                    "vendor": "Vendor",
                    "handle": "catalog-product",
                    "productType": "Shirt",
                    "status": "ACTIVE",
                    "tags": [],
                    "seo": {"title": None, "description": None},
                    "options": [{"name": "Title", "position": 1, "optionValues": [{"name": "Default Title"}]}],
                    "variants": {"nodes": [{"id": "gid://shopify/ProductVariant/1", "title": "Default Title", "sku": "BASE", "price": "10.00", "selectedOptions": [{"name": "Title", "value": "Default Title"}]}]},
                }
            }
        }

    async def get_product_metafields(self, gid: str, identifiers):
        _ = gid, identifiers
        return []

    async def create_product_options(self, product_id: str, options):
        self.created_options.append((product_id, options))
        return {"data": {"productOptionsCreate": {"userErrors": []}}}

    async def bulk_create_product_variants(self, product_id: str, variants):
        self.created_variants.append((product_id, variants))
        return {
            "data": {
                "productVariantsBulkCreate": {
                    "userErrors": [],
                    "productVariants": [
                        {"id": "gid://shopify/ProductVariant/11"},
                        {"id": "gid://shopify/ProductVariant/12"},
                    ],
                }
            }
        }

    async def set_product_variant_matrix(self, product_id: str, options, variants):
        self.created_options.append((product_id, options))
        self.created_variants.append((product_id, variants))
        return {"data": {"productSet": {"userErrors": [], "product": {
            "id": product_id,
            "options": [{"name": "Size", "position": 1, "optionValues": [{"name": "S"}, {"name": "M"}]}],
            "variants": {"nodes": [
                {"id": "gid://shopify/ProductVariant/11", "title": "S", "sku": "SKU-S", "price": "10.00", "selectedOptions": [{"name": "Size", "value": "S"}]},
                {"id": "gid://shopify/ProductVariant/12", "title": "M", "sku": "SKU-M", "price": "10.00", "selectedOptions": [{"name": "Size", "value": "M"}]},
            ]},
        }}}}

    async def update_product_from_input(self, product):
        self.updated_payload = product
        return {"data": {"productUpdate": {"userErrors": []}}}

    async def find_product_id_by_handle(self, handle: str):
        _ = handle
        return None

    async def find_product_id_by_sku(self, sku: str):
        _ = sku
        return None


class _FakeSupabaseRevert:
    def __init__(self) -> None:
        self.pending_calls = 0
        self.intelligence = self

    def get_product_intelligence_suggestion(self, suggestion_id: str, shop_domain=None):
        _ = suggestion_id, shop_domain
        return {
            "suggestion_id": "sug-1",
            "status": "applied",
            "audit_id": "audit-1",
            "product_index": 0,
            "product_title": "Catalog Product",
            "patch_payload": {"variant_operations": {"create_variants": [{"sku": "SKU-S"}]}},
            "previous_payload": {
                "variant_operations": {
                    "created_variant_ids": [
                        "gid://shopify/ProductVariant/11",
                        "gid://shopify/ProductVariant/12",
                    ]
                },
                "__revert_modes": {"variant_operations": "restore"},
                "__is_reversible": True,
            },
        }

    def get_product_intelligence_audit(self, audit_id: str, shop_domain=None):
        _ = audit_id, shop_domain
        return {
            "totals": {
                "audited_products": [
                    {
                        "id": "gid://shopify/Product/1",
                        "title": "Catalog Product",
                    }
                ]
            }
        }

    def mark_product_intelligence_suggestion_reverted(self, suggestion_id: str, shop_domain=None):
        _ = suggestion_id, shop_domain
        self.pending_calls += 1
        if self.pending_calls == 1:
            raise RuntimeError("temporary write failure")
        return {"suggestion_id": "sug-1", "status": "reverted"}


class _FakeShopifyRevert:
    def __init__(self) -> None:
        self.deleted_variants = []
        self.updated_payload = None

    async def bulk_delete_product_variants(self, product_id: str, variant_ids):
        self.deleted_variants.append((product_id, list(variant_ids)))
        return {"data": {"productVariantsBulkDelete": {"userErrors": []}}}

    async def update_product_from_input(self, product):
        self.updated_payload = product
        return {"data": {"productUpdate": {"userErrors": []}}}

    async def find_product_id_by_handle(self, handle: str):
        _ = handle
        return None

    async def find_product_id_by_sku(self, sku: str):
        _ = sku
        return None


@pytest.mark.asyncio
async def test_apply_tracks_created_variant_ids_for_revert():
    supabase = _FakeSupabaseApply()
    shopify = _FakeShopifyApply()

    result = await apply_execute(
        supabase=supabase,
        shopify=shopify,
        suggestion_id="sug-1",
        shop_domain="store.myshopify.com",
    )

    assert result["status"] == "applied"
    assert len(shopify.created_options) == 1
    assert len(shopify.created_variants) == 1
    assert shopify.updated_payload is None
    assert supabase.applied_previous_payload["variant_operations"]["created_variant_ids"] == [
        "gid://shopify/ProductVariant/11",
        "gid://shopify/ProductVariant/12",
    ]

    _, matrix_variants = shopify.created_variants[0]
    assert all("sku" in variant for variant in matrix_variants), (
        "set_product_variant_matrix path (productSet) must retain sku"
    )


@pytest.mark.asyncio
async def test_revert_deletes_created_variants_and_retries_pending_write(monkeypatch):
    async def _no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "application.use_cases.intelligence_revert_suggestion.asyncio.sleep",
        _no_sleep,
    )
    supabase = _FakeSupabaseRevert()
    shopify = _FakeShopifyRevert()

    result = await revert_execute(
        supabase=supabase,
        shopify=shopify,
        suggestion_id="sug-1",
        shop_domain="store.myshopify.com",
    )

    assert result["status"] == "reverted"
    assert shopify.updated_payload is None
    assert shopify.deleted_variants == [
        (
            "gid://shopify/Product/1",
            ["gid://shopify/ProductVariant/11", "gid://shopify/ProductVariant/12"],
        )
    ]
    assert supabase.pending_calls == 2
