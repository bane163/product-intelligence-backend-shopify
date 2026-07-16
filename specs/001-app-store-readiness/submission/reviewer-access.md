# Reviewer Access

## Review store access

- **Review store domain**: `REPLACE_BEFORE_SUBMISSION`
- **App URL**: `REPLACE_BEFORE_SUBMISSION`
- **Install path**: install from Shopify review flow into the review store

## Reviewer credentials

- **Reviewer staff email**: `REPLACE_BEFORE_SUBMISSION`
- **Secure credential handoff reference**: `REPLACE_BEFORE_SUBMISSION`
- **2FA / backup code handoff reference**: `REPLACE_BEFORE_SUBMISSION`

## Backend or support credentials

- **Support contact email**: `REPLACE_BEFORE_SUBMISSION`
- **Emergency contact name**: `REPLACE_BEFORE_SUBMISSION`
- **Emergency contact method**: `REPLACE_BEFORE_SUBMISSION`
- **Support hours / timezone**: `REPLACE_BEFORE_SUBMISSION`

## Sample data package

- **Primary upload file**: `REPLACE_BEFORE_SUBMISSION`
- **Expected result summary**: `REPLACE_BEFORE_SUBMISSION`
- **Store cleanup/reset instructions**: `REPLACE_BEFORE_SUBMISSION`

## Reviewer notes

1. Install the app and approve access from Shopify.
2. If billing is enabled for review, select a plan and wait for the in-app
   return state.
3. Use the primary sample file to validate extraction and product creation.
4. If the embedded session expires, reopen the app in Shopify Admin and retry the
   documented flow.

## Security handling

- Do not commit passwords, backup codes, or one-time codes to the repository.
- Store reviewer credentials in an approved secret manager or secure handoff tool,
  and record only the retrieval reference above.
- Rotate reviewer credentials after each submission cycle.
- Do not reuse production merchant credentials.
- Remove any stale staff accounts before resubmission.
