import pytest

from application.services.inventory_execution import (
    ACTIVATE_MUTATION,
    INVENTORY_ITEM_QUERY,
    PRODUCT_INVENTORY_QUERY,
    SET_MUTATION,
    apply_inventory_operations,
    assert_inventory_expectations,
)


def operation(**overrides):
    return {
        "operation_id": "00000000-0000-0000-0000-000000000001",
        "change_id": "quantity-red",
        "subject": "Size: S / Color: Red",
        "subject_identity": {
            "option_names": ["size", "color"],
            "values": ["s", "red"],
        },
        "location": {"id": "gid://shopify/Location/1", "name": "Main"},
        "proposed_quantity": 50,
        "approved_current_quantity": 10,
        "approved_current_tracked": True,
        "prior_stocked": True,
        "inventory_item_id": "gid://shopify/InventoryItem/1",
        **overrides,
    }


@pytest.mark.asyncio
async def test_inventory_preflight_rejects_stale_available_quantity():
    class Client:
        async def graphql(self, query, variables=None):
            assert query == INVENTORY_ITEM_QUERY
            return {
                "data": {
                    "inventoryItem": {
                        "id": variables["id"],
                        "tracked": True,
                        "inventoryLevel": {
                            "quantities": [{"name": "available", "quantity": 11}]
                        },
                    }
                }
            }

    with pytest.raises(RuntimeError, match="INVENTORY_STALE"):
        await assert_inventory_expectations(Client(), [operation()])


@pytest.mark.asyncio
async def test_stocked_inventory_uses_option_identity_cas_and_idempotency():
    calls = []

    class Client:
        async def graphql(self, query, variables=None):
            calls.append((query, variables))
            if query == PRODUCT_INVENTORY_QUERY:
                return {
                    "data": {
                        "product": {
                            "variants": {
                                "nodes": [
                                    {
                                        "id": "gid://shopify/ProductVariant/1",
                                        "selectedOptions": [
                                            {"name": "Size", "value": "S"},
                                            {"name": "Color", "value": "Red"},
                                        ],
                                        "inventoryItem": {
                                            "id": "gid://shopify/InventoryItem/1",
                                            "tracked": True,
                                            "inventoryLevel": {
                                                "id": "level",
                                                "quantities": [
                                                    {
                                                        "name": "available",
                                                        "quantity": 10,
                                                    }
                                                ],
                                            },
                                        },
                                    }
                                ]
                            }
                        }
                    }
                }
            assert query == SET_MUTATION
            return {"data": {"inventorySetQuantities": {"userErrors": []}}}

    applied = await apply_inventory_operations(
        Client(), plan_id="plan-1", product_id="product-1", operations=[operation()]
    )
    assert applied[0]["available"] == 50
    variables = calls[1][1]
    assert variables["idempotencyKey"] == operation()["operation_id"]
    assert variables["input"]["quantities"][0]["changeFromQuantity"] == 10
    assert (
        variables["input"]["referenceDocumentUri"]
        == "gid://stockpile/MutationPlan/plan-1"
    )
    assert "ignoreCompareQuantity" not in variables["input"]


@pytest.mark.asyncio
async def test_unstocked_inventory_activates_with_reviewed_initial_available():
    calls = []

    class Client:
        async def graphql(self, query, variables=None):
            calls.append((query, variables))
            if query == PRODUCT_INVENTORY_QUERY:
                return {
                    "data": {
                        "product": {
                            "variants": {
                                "nodes": [
                                    {
                                        "selectedOptions": [
                                            {"name": "Title", "value": "Default Title"}
                                        ],
                                        "inventoryItem": {
                                            "id": "item-1",
                                            "tracked": True,
                                            "inventoryLevel": None,
                                        },
                                    }
                                ]
                            }
                        }
                    }
                }
            assert query == ACTIVATE_MUTATION
            return {"data": {"inventoryActivate": {"userErrors": []}}}

    await apply_inventory_operations(
        Client(),
        plan_id="plan",
        product_id="product",
        operations=[
            operation(
                subject_identity={
                    "option_names": ["title"],
                    "values": ["default title"],
                },
                prior_stocked=False,
                approved_current_quantity=None,
            )
        ],
    )
    assert calls[1][1]["available"] == 50
