"""Concurrency-safe execution of approved mutation-plan inventory operations."""

from __future__ import annotations

from typing import Any


INVENTORY_ITEM_QUERY = """query MutationPlanInventoryExpectation($id: ID!, $locationId: ID!) {
  inventoryItem(id: $id) { id tracked inventoryLevel(locationId: $locationId) {
    id quantities(names: ["available"]) { name quantity }
  } }
}"""
PRODUCT_INVENTORY_QUERY = """query MutationPlanInventoryTargets($id: ID!, $locationId: ID!) {
  product(id: $id) { variants(first: 250) { nodes {
    id selectedOptions { name value }
    inventoryItem { id tracked inventoryLevel(locationId: $locationId) {
      id quantities(names: ["available"]) { name quantity }
    } }
  } pageInfo { hasNextPage endCursor } } }
}"""
PRODUCT_INVENTORY_PAGE_QUERY = """query MutationPlanInventoryTargetsPage($id: ID!, $locationId: ID!, $after: String!) {
  product(id: $id) { variants(first: 250, after: $after) { nodes {
    id selectedOptions { name value }
    inventoryItem { id tracked inventoryLevel(locationId: $locationId) {
      id quantities(names: ["available"]) { name quantity }
    } }
  } pageInfo { hasNextPage endCursor } } }
}"""
ACTIVATE_MUTATION = """mutation MutationPlanInventoryActivate(
  $inventoryItemId: ID!, $locationId: ID!, $available: Int!, $idempotencyKey: String!
) {
  inventoryActivate(inventoryItemId: $inventoryItemId, locationId: $locationId, available: $available) @idempotent(key: $idempotencyKey) {
    inventoryLevel { id }
    userErrors { field message }
  }
}"""
SET_MUTATION = """mutation MutationPlanInventorySet(
  $input: InventorySetQuantitiesInput!, $idempotencyKey: String!
) {
  inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    inventoryAdjustmentGroup { createdAt }
    userErrors { code field message }
  }
}"""


def _available(level: Any) -> int | None:
    quantities = level.get("quantities") if isinstance(level, dict) else []
    return next(
        (
            entry.get("quantity")
            for entry in quantities
            if isinstance(entry, dict) and entry.get("name") == "available"
        ),
        None,
    )


def _errors(result: dict[str, Any], mutation: str) -> list[dict[str, Any]]:
    payload = result.get("data", {}).get(mutation, {})
    errors = payload.get("userErrors") if isinstance(payload, dict) else None
    return (
        [error for error in errors if isinstance(error, dict)]
        if isinstance(errors, list)
        else []
    )


async def assert_inventory_expectations(
    client: Any, operations: list[dict[str, Any]]
) -> None:
    """Reject all product writes when reviewed existing inventory has changed."""
    for operation in operations:
        inventory_item_id = operation.get("inventory_item_id")
        if not inventory_item_id:
            continue  # New variants do not exist until productSet succeeds.
        location_id = operation.get("location", {}).get("id")
        result = await client.graphql(
            INVENTORY_ITEM_QUERY, {"id": inventory_item_id, "locationId": location_id}
        )
        item = result.get("data", {}).get("inventoryItem")
        if not isinstance(item, dict):
            raise RuntimeError(
                "INVENTORY_STALE: reviewed inventory item no longer exists"
            )
        level = item.get("inventoryLevel")
        if (
            bool(level) != bool(operation.get("prior_stocked"))
            or _available(level) != operation.get("approved_current_quantity")
            or bool(item.get("tracked"))
            != bool(operation.get("approved_current_tracked"))
        ):
            raise RuntimeError(
                "INVENTORY_STALE: Shopify inventory changed after review"
            )


def _identity(variant: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            str(pair.get("name") or "").strip().casefold(),
            str(pair.get("value") or "").strip().casefold(),
        )
        for pair in variant.get("selectedOptions", [])
        if isinstance(pair, dict)
    )


def _operation_identity(operation: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    identity = operation.get("subject_identity")
    if not isinstance(identity, dict):
        return ()
    names, values = identity.get("option_names"), identity.get("values")
    if not isinstance(names, list) or not isinstance(values, list):
        return ()
    return tuple(
        (str(name).casefold(), str(value).casefold())
        for name, value in zip(names, values)
    )


async def apply_inventory_operations(
    client: Any,
    *,
    plan_id: str,
    product_id: str,
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not operations:
        return []
    location_id = operations[0].get("location", {}).get("id")
    result = await client.graphql(
        PRODUCT_INVENTORY_QUERY, {"id": product_id, "locationId": location_id}
    )
    connection = result.get("data", {}).get("product", {}).get("variants", {})
    variants = list(connection.get("nodes", [])) if isinstance(connection, dict) else []
    page_info = connection.get("pageInfo") or {} if isinstance(connection, dict) else {}
    while (
        page_info.get("hasNextPage")
        and page_info.get("endCursor")
        and len(variants) <= 2048
    ):
        page_result = await client.graphql(
            PRODUCT_INVENTORY_PAGE_QUERY,
            {
                "id": product_id,
                "locationId": location_id,
                "after": page_info["endCursor"],
            },
        )
        page = page_result.get("data", {}).get("product", {}).get("variants", {})
        variants.extend(
            node for node in page.get("nodes", []) if isinstance(node, dict)
        )
        page_info = page.get("pageInfo") or {}
    variants = [variant for variant in variants if isinstance(variant, dict)]
    applied: list[dict[str, Any]] = []
    for operation in operations:
        expected = _operation_identity(operation)
        matches = [
            variant
            for variant in variants
            if expected and _identity(variant) == expected
        ]
        if not matches and len(variants) == len(operations) == 1:
            matches = variants
        if len(matches) != 1:
            raise RuntimeError(
                f"Inventory variant match failed for {operation.get('subject')}"
            )
        item = matches[0].get("inventoryItem")
        if (
            not isinstance(item, dict)
            or not item.get("id")
            or item.get("tracked") is not True
        ):
            raise RuntimeError(
                f"Inventory tracking was not enabled for {operation.get('subject')}"
            )
        level = item.get("inventoryLevel")
        quantity = int(operation["proposed_quantity"])
        variables = {
            "inventoryItemId": item["id"],
            "locationId": location_id,
            "available": quantity,
            "idempotencyKey": operation["operation_id"],
        }
        if not isinstance(level, dict):
            mutation_result = await client.graphql(ACTIVATE_MUTATION, variables)
            errors = _errors(mutation_result, "inventoryActivate")
        else:
            mutation_result = await client.graphql(
                SET_MUTATION,
                {
                    "idempotencyKey": operation["operation_id"],
                    "input": {
                        "name": "available",
                        "reason": "correction",
                        "referenceDocumentUri": f"gid://stockpile/MutationPlan/{plan_id}",
                        "quantities": [
                            {
                                "inventoryItemId": item["id"],
                                "locationId": location_id,
                                "quantity": quantity,
                                "changeFromQuantity": (
                                    operation.get("approved_current_quantity")
                                    if operation.get("inventory_item_id")
                                    else _available(level)
                                ),
                            }
                        ],
                    },
                },
            )
            errors = _errors(mutation_result, "inventorySetQuantities")
        if errors:
            raise RuntimeError(
                "; ".join(
                    str(error.get("message") or "Inventory update failed")
                    for error in errors
                )
            )
        applied.append(
            {
                "change_id": operation.get("change_id"),
                "variant": operation.get("subject"),
                "location": operation.get("location"),
                "available": quantity,
            }
        )
    return applied
