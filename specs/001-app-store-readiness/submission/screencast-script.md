# Screencast Script

## Goal

Show a reviewer the exact merchant journey they can reproduce inside Shopify
Admin without narration gaps or hidden setup steps.

## Recording checklist

- Use the review store from `reviewer-access.md`
- Confirm the store is reset to a clean state
- Have the sample upload file ready
- Close unrelated browser tabs and notifications
- Use the deployed review environment, not local development

## Suggested runtime

4 to 6 minutes

## Script

1. **Opening context**
   - Show the app installed in Shopify Admin.
   - State that Stockpile helps merchants upload product files, review
     extracted data, and create Shopify products.

2. **Landing experience**
   - Open the embedded app workspace.
   - Briefly show the main navigation: Imports, Catalog health, Activity,
     History, Settings, Billing.

3. **Billing overview**
   - Open Billing.
   - Show the current plan and pricing.
   - If review requires plan selection, demonstrate the plan-selection screen and
     return back into the app.

4. **Core workflow**
   - Open Documents.
   - Upload the prepared sample file.
   - Show extraction progress and the review state.
   - Highlight that the user can inspect data before creating products.

5. **Shopify verification**
   - Complete product submission.
   - Switch to Shopify Admin products and show the created product entries.

6. **Trust and recovery**
   - Briefly show a billing or workflow recovery state if available in the review
     build.
   - Point out that support details and reviewer instructions are included in the
     submission package.

7. **Closing**
   - Return to the embedded app and end on a stable, authenticated screen.

## Capture notes

- Keep cursor movement deliberate.
- Do not show raw secrets, local URLs, or developer tools.
- Re-record if the flow includes unexpected waiting or stale test data.
