Shopify Admin GraphQL — Products Guideline
=========================================

Purpose
-------
This guideline documents recommended GraphQL operations, patterns, and mappings for product creation, updating, and querying in the backend. It is intended to be easily discoverable by the assistant and developers; file name includes `SHOPIFY` and `PRODUCTS` to aid quick lookup.

Scope
-----
- Admin GraphQL API (products only)
- Creation, update, fetch, and deletion of products and related nested resources (variants, images, metafields)
- Mapping from document extraction outputs to Shopify product fields

Location
--------
- File: SHOPIFY_PRODUCTS_GRAPHQL_GUIDELINE.md (repo root)
- Use this file to find canonical query/mutation examples and best-practices for backend implementations.

Authentication
--------------
- Use the Admin GraphQL endpoint: https://{shop}.myshopify.com/admin/api/{version}/graphql.json
- Use header: X-Shopify-Access-Token: <admin-access-token>
- Set Content-Type: application/json
- Use GraphQL variables rather than string interpolation to avoid injection and make caching readable.

Rate limits & throttling
------------------------
- GraphQL Admin API uses cost-based throttling. Inspect the response `extensions` (cost info) for consumed cost and remaining budget.
- Best practices:
  - Request only fields you need (minimize cost).
  - Batch and paginate rather than pulling everything at once.
  - Use exponential backoff and respect Retry-After headers where applicable.
  - For large imports/exports, use Bulk Operations API (bulkOperationRunMutation) rather than many single mutations.

IDs and identifiers
-------------------
- Shopify uses global GraphQL IDs (GIDs) like `gid://shopify/Product/1234567890`.
- When creating resources, use the returned GID for subsequent updates or relations.
- productByHandle(handle: "...") can be used to locate products by handle when GID is unknown.

General conventions
-------------------
- Always use variables in requests.
- Normalize and validate document-extracted values before sending (currency, SKU formats, image URLs).
- Use `userErrors` and `extensions` in mutation responses to detect and surface validation/permission errors.
- Store returned GIDs and timestamps for idempotency and reconciliation.

Products — Queries
------------------
1) Query product by GID (single product):

Query example (variables):
```
query ProductById($id: ID!) {
  product(id: $id) {
    id
    title
    handle
    descriptionHtml
    vendor
    productType
    tags
    publishedAt
    options {
      id
      name
      values
    }
    variants(first: 10) {
      nodes {
        id
        sku
        price
        title
        inventoryQuantity
        barcode
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
    images(first: 10) {
      nodes {
        id
        src
        altText
      }
    }
    metafields(first: 20) {
      nodes {
        id
        namespace
        key
        value
        type
      }
    }
  }
}
```

2) Query products list with pagination:
- Use `products(first: <n>, after: <cursor>)` and inspect `pageInfo`.
- Prefer limiting nested connection sizes to avoid heavy responses.

Products — Mutations
--------------------
1) Create product (productCreate)
- Use `productCreate(input: ProductInput!)`.
- Map extracted fields to ProductInput fields (see the "Document extraction mapping" section).

Mutation example (variables):
```
mutation CreateProduct($input: ProductInput!) {
  productCreate(input: $input) {
    product {
      id
      title
      handle
    }
    userErrors {
      field
      message
    }
  }
}
```

Minimum recommended fields in input from document extraction:
- title (string)
- bodyHtml (string) — sanitized HTML or plain text converted to safe HTML
- vendor (optional)
- productType (optional)
- tags (array of strings)
- variants: array (at least one) with price, sku, inventoryPolicy, inventoryManagement
- images: array with src (absolute URLs) and optional altText
- metafields: for extracted structured attributes not mapped to core fields

2) Update product (productUpdate)
- Use `productUpdate(id: ID!, input: ProductInput!)`.
- When updating variants, prefer variant-specific mutations (productVariantUpdate/productVariantCreate/productVariantDelete) if only variants change.

3) Delete product (productDelete)
- Use `productDelete(id: ID!)` and handle `userErrors`.

4) Variant & image helpers
- productVariantCreate/productVariantUpdate/productVariantDelete exist for fine-grained variant operations.
- productImageCreate/productImageDelete for image operations.

Bulk operations
---------------
- For large-scale imports/updates from document extraction, prefer the Bulk Operations API via `bulkOperationRunMutation`.
- Use CSV or JSON-ready payloads in bulk operations; watch for completion via `bulkOperationRunQuery` status.

Document extraction -> Shopify mapping
-------------------------------------
Map common extracted fields to Shopify product fields:
- title -> title
- short/long descriptions -> bodyHtml (sanitize/limit size)
- price -> variants[0].price (and/or create multiple variants when options exist)
- sku -> variants[].sku
- inventory counts -> variant inventoryQuantity and inventoryManagement
- images -> images[] (ensure accessible absolute URLs; dedupe by checksum)
- categories/taxonomy -> productType and tags
- attributes / custom fields -> metafields (namespace: e.g., "extraction", key: "original_author")
- GTIN/UPC/EAN/barcode -> variant.barcode or metafields if multiple

Idempotency & deduplication
---------------------------
- Detect existing product by handle, SKU, or GTIN before creating to avoid duplicates.
- Prefer to update an existing product if match confidence is high.
- Keep a log of original document -> Shopify GID mappings for reconciliation and re-runs.

Validation & sanitization
-------------------------
- Sanitize HTML (bodyHtml) and strip any unsafe tags/attributes.
- Validate currency and convert to correct decimal format.
- Ensure SKUs conform to store rules and length limits.
- Validate image URLs and optionally fetch/verify content type before submission.

Error handling
--------------
- Inspect `userErrors` in mutation payloads and log both `field` and `message`.
- Inspect `extensions` for rate cost and throttling information.
- For transient errors or rate-limit responses, use exponential backoff and retries.
- For permanent validation errors, surface them to extraction pipeline for correction.

Testing recommendations
-----------------------
- Use a development/test shop with limited data for end-to-end tests.
- Mock GraphQL responses for unit tests of mapping logic.
- Include tests for:
  - Creation flow with minimal and full input
  - Update flow and conflict resolution
  - Error handling for userErrors and throttling

Examples — curl (create)
------------------------
curl -X POST "https://{shop}.myshopify.com/admin/api/{version}/graphql.json" \
  -H "Content-Type: application/json" \
  -H "X-Shopify-Access-Token: <token>" \
  -d '{"query":"mutation CreateProduct($input: ProductInput!) { productCreate(input: $input) { product { id title handle } userErrors { field message } } }","variables":{"input":{"title":"Example product","bodyHtml":"<p>From document extraction</p>","variants":[{"price":"19.99","sku":"DOC-001"}],"images":[{"src":"https://example.com/img1.jpg"}]}}'}

Quick tips
----------
- Prefer product handles derived from sanitized title for idempotent lookups: e.g., `slugify(title)`.
- Keep requests minimal and rely on pagination for lists.
- Store mapping metadata (source_document_id -> Shopify GID) for traceability.

References
----------
- Shopify Admin GraphQL docs: https://shopify.dev/docs/api/admin-graphql/latest (use as authoritative reference for field-level details and new capabilities)

Maintainer notes
----------------
- Update this file when GraphQL API versions change or when adding additional resource mappings (collections, custom apps integration).
- To make this file discoverable by the assistant, search for files prefixed with `SHOPIFY_` or containing `GRAPHQL`.

End of guideline
