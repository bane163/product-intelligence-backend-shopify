# Contract: Reviewer Journey

## Purpose

Define the minimum end-to-end behavior that Shopify reviewers and internal QA
must be able to execute for approval.

## Journey Contract

### 1. Install and authenticate

- **Entry point**: Shopify-initiated install flow only
- **Expected behavior**:
  - Merchant/reviewer is not asked to enter a store domain manually.
  - OAuth completes before any app UI interaction.
  - Successful approval lands the user in the embedded app workspace.
- **Failure contract**:
  - Any auth failure shows an actionable recovery path.
  - Reinstall must return to a clean, valid embedded auth state.

### 2. Billing and access state

- **Entry point**: Embedded app workspace or billing plan selection page
- **Expected behavior**:
  - Reviewers can see current plan/trial status and choose or change a plan
    without reinstalling the app or contacting support.
  - Billing approval returns the user to the app in a recognizable state.
  - Declines, expired trials, or inactive plans show clear next actions.
- **Failure contract**:
  - Billing failures never produce a raw 4xx/5xx reviewer-facing page.
  - Reviewers can determine whether the change is pending, active, or declined.

### 3. Core workflow

- **Entry point**: Authenticated workspace with a valid plan or active trial
- **Expected behavior**:
  - Reviewer uploads a supported source file.
  - App shows progress through extraction/review states.
  - Reviewer can review and create products in Shopify.
  - Result in Shopify matches what the app presented.
- **Failure contract**:
  - Long-running work surfaces progress and recovery actions.
  - Partial failures do not leave reviewers unsure whether products were created.

### 4. Reviewer support package

- **Expected behavior**:
  - Reviewer receives valid credentials, sample files, testing instructions, and
    a screencast aligned with the live app.
  - Support or emergency contact information is current and reachable.
- **Failure contract**:
  - Missing credentials, stale instructions, or misaligned listing/billing info
    are treated as contract failures that block readiness.
