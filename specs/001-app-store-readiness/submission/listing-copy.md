# App Store Listing Copy

## Canonical app identity

- **App name**: Stockpile
- **Primary category**: Product feeds
- **Secondary category**: Store data importer
- **Support email**: `REPLACE_BEFORE_SUBMISSION`
- **Support URL**: `REPLACE_BEFORE_SUBMISSION`
- **Privacy policy URL**: `REPLACE_BEFORE_SUBMISSION`
- **App URL**: `REPLACE_BEFORE_SUBMISSION`

## One-line value proposition

Stockpile turns spreadsheets and supplier documents into reviewable Shopify
products so merchants can clean, verify, and publish catalog data faster.

## Short description

Turn supplier spreadsheets and documents into source-backed product drafts,
review every change, and publish the approved catalog to Shopify.

## Full description draft

Stockpile helps merchants move product data from spreadsheets and supplier
documents into Shopify without manual copy-paste.

Core workflow:

1. Upload a supported file from the embedded Shopify app.
2. Review extracted product information before it is written to Shopify.
3. Open a source link to verify the supporting cell or document location.
4. Publish approved products and confirm the result in Shopify Admin.

Included capabilities reflected in the live app:

- Guided extraction from spreadsheet-style product files
- Source-backed review before product creation
- Catalog health suggestions
- Shopify-managed billing inside the app
- Clear recovery messaging for auth, processing, and billing states

## Pricing copy

Use exactly this pricing unless billing configuration changes in
`extractor-v3/app/config/billing.ts`.

| Plan | Monthly price | Included files | Overage |
| --- | --- | --- | --- |
| Starter | $79 | 80 / billing cycle | $0.85 per file |
| Growth | $219 | 250 / billing cycle | $0.85 per file |
| Scale | $599 | 700 / billing cycle | $0.85 per file |

- **Trial**: 5-day free trial
- **Overage cap**: $500 per billing cycle
- **Billing provider**: Shopify Billing

## Review-copy guardrails

- Do not claim marketplace syncs, channels, or automations the app does not
  currently expose in the embedded experience.
- Do not promise fully automatic product publishing without review.
- Keep plan names and prices identical to in-app billing surfaces.
- Keep the app name identical to `extractor-v3/shopify.app.toml`.

## Assets checklist

- **App icon**: text-free stacked catalog-sheet mark on warm neutral ground
- **Hero/marketing screenshots**: see `screenshot-shotlist.md`
- **Screencast**: see `screencast-script.md`
