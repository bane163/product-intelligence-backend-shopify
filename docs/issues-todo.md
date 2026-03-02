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
- **Status:** `done`
- **Area:** Frontend
- **Likely files:** `extractor-v3/app/routes/app.tsx`

## 6) Replace widespread inline styles with maintainable styling
- **Status:** `done`
- **Area:** Frontend
- **Likely files:** `FileCard.tsx`, `ProductIntelligenceGrid.tsx`, `app.settings.tsx`, `app._index.tsx`
- **Details:** Migrated inline styles to CSS modules across documents/settings/import/intelligence/run-logs/product-details surfaces. Remaining inline styles are intentionally limited to deferred `app/components/polaris-compat.tsx` and one dynamic tooltip-position style in `InfoHint.tsx`.

## 7) Product intelligence view icon opens Shopify Admin product in new tab
- **Status:** `done`
- **Area:** Frontend
- **Likely files:** `extractor-v3/app/features/data-intelligence/components/ProductIntelligenceGrid.tsx`
- **Details:** Added external-link icon in row actions; derives admin URL from product GID + shop domain.

## 8) Add skeleton loading states to reduce flicker
- **Status:** `done`
- **Area:** Frontend
- **Likely files:** `LoadingSkeleton.tsx`, documents/product-intelligence/product-details/settings routes
- **Details:** Expanded LoadingSkeleton with animated shimmer bars (list/detail/form variants). Added to settings initial load, product details loading, and intelligence grid initial load.

## 9) Disable Run Audit when product audit already in progress
- **Status:** `done`
- **Area:** Frontend
- **Likely files:** product details route + data-intelligence hooks/components
- **Details:** Row audit button disabled when row has active run status (queued/running). Bulk audit button disabled when any selected product has active run. Product details page audit button also gated.

## 10) Backend refactor for DRY and maintainability
- **Status:** `done`
- **Area:** Backend
- **Likely files:** `application/use_cases/**`, `application/services/**`, shared utility extraction targets
- **Details:** Extracted `resolve_shopify_client` to shared `api/agents/utils.py`, removed duplicate from submit.py and intelligence.py. Removed dead `_process_shopify_submit` from offload worker + 2 associated tests.

## 11) Disable Submit to Shopify for already submitted documents
- **Status:** `done`
- **Area:** Frontend
- **Likely files:** `extractor-v3/app/features/imports/hooks/useImportWorkflow.ts`, `extractor-v3/app/routes/app.excel-upload.tsx`, `extractor-v3/app/components/ExcelSteps/Confirmation.tsx`
- **Details:** Prevent submit button from rendering/enabling when viewing a read-only submitted document to avoid duplicate submissions.

## 12) Navigate to submitted tab after submit success
- **Status:** `done`
- **Area:** Frontend
- **Likely files:** `extractor-v3/app/features/imports/hooks/useImportWorkflow.ts`, `extractor-v3/app/routes/app._index.tsx`
- **Details:** Ensure successful submission navigates user to `/app/documents?tab=submitted` (already triggered?) and update navigation logic if needed for clarity.

## 13) Redesign Step 4 (Review source) screen
- **Status:** `done`
- **Area:** Frontend
- **Likely files:** `extractor-v3/app/components/ExcelSteps/Confirmation.tsx`, `extractor-v3/app/components/ExcelSteps/ExtractedProductsGrid.tsx`
- **Details:** Reduce the number of rectangle borders, simplify card styling, and align the layout with the desired visual direction for the final review step. Ensure accessibility and responsive spacing.

## 14) Allow uploading multiple files at once
- **Status:** `done`
- **Area:** Frontend + Backend
- **Likely files:** `extractor-v3/app/routes/app.excel-upload.tsx`, upload API endpoint(s), import workflow service/hooks
- **Details:** Implemented `POST /agents/upload/bulk` with per-file success/error payloads and batched metadata persistence. Frontend upload step now supports multi-file selection, submits a single bulk request, shows per-file upload statuses, and navigates back to Documents after multi-upload completion.

## 15) Disable Start Over button at step 1 in import wizard
- **Status:** `done`
- **Area:** Frontend
- **Likely files:** `extractor-v3/app/routes/app.excel-upload.tsx`, `extractor-v3/app/features/imports/hooks/useImportWorkflow.ts`
- **Details:** Disable the Start Over button when the user is already on step 1 to avoid a no-op action and reduce confusion.

## 16) Fix "Apply fixes" Shopify metafields GraphQL failure
- **Status:** `done`
- **Area:** Backend
- **Likely files:** apply-fixes flow/service, Shopify GraphQL query definitions for product metafields
- **Details:** `Apply fixes` currently fails with: `Applied 0, failed 1: Shopify GraphQL error: HasMetafieldsIdentifier isn't a defined input type (on $identifiers) (code=variableRequiresValidType) | Field 'metafields' doesn't accept argument 'identifiers' (code=argumentNotAccepted) | Field 'namespace' doesn't exist on type 'MetafieldConnection' (code=undefinedField) | Field 'key' doesn't exist on type 'MetafieldConnection' (code=undefinedField) | Field 'value' doesn't exist on type 'MetafieldConnection' (code=undefinedField) | Field 'type' doesn't exist on type 'MetafieldConnection' (code=undefinedField) | Variable $identifiers is declared by productMetafields but not used (code=variableNotUsed) | request_id=006573d3-1c82-4fdb-a9ac-575c8b06a8a5-1772379589`

## 17) Show view/edit icons only on row hover in product intelligence grid
- **Status:** `done`
- **Area:** Frontend
- **Likely files:** `extractor-v3/app/features/data-intelligence/components/ProductIntelligenceGrid.tsx`
- **Details:** On the product intelligence landing page, display the view and edit row action icons only when the user hovers a product row (while keeping keyboard accessibility behavior intact).

## 18) Keep product intelligence skeleton until grid rows are ready
- **Status:** `done`
- **Area:** Frontend
- **Likely files:** `extractor-v3/app/features/data-intelligence/components/ProductIntelligenceGrid.tsx`, product intelligence route/loading state hooks
- **Details:** Keep the landing-page skeleton visible until all product intelligence components are ready, including the rows in the products grid, to avoid partial-load flicker.

## 19) Create custom skeleton for documents landing page
- **Status:** `done`
- **Area:** Frontend
- **Likely files:** documents landing route/components, `LoadingSkeleton.tsx` (or route-specific skeleton component)
- **Details:** Use a custom skeleton that matches the documents landing page layout exactly, and only skeletonize sections that are still loading.

## 20) Seed default LLM provider configs on merchant install (secure API keys)
- **Status:** `done`
- **Area:** Backend + Supabase
- **Likely files:** app install flow/webhook handler, LLM configuration service/repository, Supabase migration/seed scripts
- **Details:** When a merchant installs the app, create or upsert two default LLM config records (Ollama Cloud and OpenAI) for that merchant, while keeping API keys out of plaintext table fields (use server-side secret storage/reference only).

## 21) Simplify submitted-doc review columns and submitted-tab actions
- **Status:** `done`
- **Area:** Frontend
- **Likely files:** submitted documents review/grid components (e.g., `extractor-v3/app/components/ExcelSteps/ExtractedProductsGrid.tsx`), documents page submitted-tab row actions
- **Details:** In submitted-document Review view, remove the source icon column and show only Title, Vendor, SKU, and Price. Since submitted documents are view-only, remove the edit icon in the Submitted tab; on row hover, show only eye and trash icons.
