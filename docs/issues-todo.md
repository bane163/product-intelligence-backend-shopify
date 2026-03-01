# Frontend + Backend Issues To-Do

Status legend: `todo` | `in_progress` | `done`

## 1) Remove file-card top overlay loader (keep bottom progress message)
- **Status:** `done`
- **Area:** Frontend
- **Likely files:** `extractor-v3/app/components/FileCard.tsx`

## 2) Fix Shopify submit failure (`All connection attempts failed`)
- **Status:** `done`
- **Area:** Backend
- **Likely files:** `api/agents/submit.py`, `application/use_cases/processing/submit_products.py`, `shopify.py`

## 3) Remove submit offload/queue behavior
- **Status:** `done`
- **Area:** Frontend + Backend
- **Likely files:** `extractor-v3/app/features/imports/services/importApi.ts`, `api/agents/submit.py`, submit tests

## 4) Improve submit flow for bulk product creation/update
- **Status:** `done`
- **Area:** Backend
- **Likely files:** `application/use_cases/processing/submit_products.py`, `shopify.py`, `graphql/*.graphql`

## 5) Remove separate Import item from main nav
- **Status:** `todo`
- **Area:** Frontend
- **Likely files:** `extractor-v3/app/routes/app.tsx`

## 6) Replace widespread inline styles with maintainable styling
- **Status:** `todo`
- **Area:** Frontend
- **Likely files:** `FileCard.tsx`, `ProductIntelligenceGrid.tsx`, `app.settings.tsx`, `app._index.tsx`
- **Details:** Extracted 25 inline styles from ProductIntelligenceGrid to CSS module, 8 from FileCard to CSS module. Remaining 5 are minimal dynamic/layout styles.

## 7) Product intelligence view icon opens Shopify Admin product in new tab
- **Status:** `todo`
- **Area:** Frontend
- **Likely files:** `extractor-v3/app/features/data-intelligence/components/ProductIntelligenceGrid.tsx`
- **Details:** Added external-link icon in row actions; derives admin URL from product GID + shop domain.

## 8) Add skeleton loading states to reduce flicker
- **Status:** `todo`
- **Area:** Frontend
- **Likely files:** `LoadingSkeleton.tsx`, documents/product-intelligence/product-details/settings routes
- **Details:** Expanded LoadingSkeleton with animated shimmer bars (list/detail/form variants). Added to settings initial load, product details loading, and intelligence grid initial load.

## 9) Disable Run Audit when product audit already in progress
- **Status:** `todo`
- **Area:** Frontend
- **Likely files:** product details route + data-intelligence hooks/components
- **Details:** Row audit button disabled when row has active run status (queued/running). Bulk audit button disabled when any selected product has active run. Product details page audit button also gated.

## 10) Backend refactor for DRY and maintainability
- **Status:** `todo`
- **Area:** Backend
- **Likely files:** `application/use_cases/**`, `application/services/**`, shared utility extraction targets
- **Details:** Extracted `resolve_shopify_client` to shared `api/agents/utils.py`, removed duplicate from submit.py and intelligence.py. Removed dead `_process_shopify_submit` from offload worker + 2 associated tests.
