# Review Environment Reference

## Purpose

Use this document to describe the exact environment a Shopify reviewer should use
 for install, billing, upload, extraction, and product-creation validation.

## Environment checklist

- **Frontend app URL**: `REPLACE_BEFORE_SUBMISSION`
- **Backend API URL**: `REPLACE_BEFORE_SUBMISSION`
- **Review store domain**: `REPLACE_BEFORE_SUBMISSION`
- **Installed app configuration**: embedded Shopify Admin app
- **Billing mode**: `REPLACE_BEFORE_SUBMISSION` (`test` or `production review`)
- **Sample upload file**: `REPLACE_BEFORE_SUBMISSION`
- **Shopify app name shown to reviewer**: `Stockpile`
- **Expected first in-app route after install**: `/app/documents`
- **Expected billing plans**:
  - Starter — `$79/month`, `80` included files
  - Growth — `$219/month`, `250` included files
  - Scale — `$599/month`, `700` included files
  - Overage — `$0.85/file`, capped at `$500/cycle`
  - Trial — `5` days

## Reviewer expectations

1. Install the app from the review store.
2. Re-open the app inside Shopify Admin if the embedded session is interrupted.
3. If the review path includes billing, choose a plan and confirm the app returns
   to the billing flow with an approval, pending, cancelled, or declined state
   instead of a blank or broken page.
4. Open **Documents**, upload the provided sample file, and wait for
   extraction/review completion.
5. Confirm the extracted product data is visible in the app review workflow.
6. Submit products and confirm the created products in Shopify Admin.

## Known evidence locations

- Reviewer dry run log: `reviewer-dry-run.md`
- Reviewer access instructions: `reviewer-access.md`
- Listing copy: `listing-copy.md`
- Submission checklist: `submission-checklist.md`

## Open items before submission

- Replace all `REPLACE_BEFORE_SUBMISSION` placeholders with deployed environment values.
- Confirm review-store data is reset to a clean state before recording the screencast.
- Confirm billing behavior matches the instructions in the reviewer package.
- Confirm the sample upload file still produces a reviewer-safe happy path.
