# Data Model: Shopify App Store Readiness

## 1. Review Environment

- **Purpose**: Represents the specific review-ready deployment and store context
  used for Shopify App Store validation.
- **Key fields**:
  - `environment_name` — human-readable identifier (`review`, `staging`, `prod`)
  - `shop_domain` — review store domain
  - `frontend_url` — embedded app entry URL
  - `backend_url` — reachable API base URL
  - `status` — `draft`, `prepared`, `active`, `expired`
  - `last_verified_at` — latest successful end-to-end verification timestamp
- **Relationships**:
  - Has many **Reviewer Credentials**
  - Has one or more **Review Journey Runs**
  - Has many **Approval Gaps**
- **Validation rules**:
  - `shop_domain`, `frontend_url`, and `backend_url` are required for any status
    beyond `draft`.
  - `active` requires all critical approval gaps to be closed.

## 2. Reviewer Credential

- **Purpose**: Captures the access a Shopify reviewer needs to validate the full
  app workflow.
- **Key fields**:
  - `email_or_identifier`
  - `role`
  - `grants_full_feature_access` — boolean
  - `delivery_channel` — where the credential is supplied in the review package
  - `expires_at`
  - `status` — `draft`, `valid`, `expired`, `revoked`
- **Relationships**:
  - Belongs to a **Review Environment**
  - Supports a **Submission Package**
- **Validation rules**:
  - `valid` credentials must have non-expired access and full feature coverage.
  - At least one valid reviewer credential is required before submission.

## 3. Merchant Subscription State

- **Purpose**: Represents the merchant billing state that gates reviewer and
  merchant access to paid functionality.
- **Key fields**:
  - `shop_domain`
  - `plan_name`
  - `status` — `trial`, `active`, `declined`, `expired`, `cancelled`
  - `trial_ends_at`
  - `cycle_ends_at`
  - `files_processed`
  - `files_included`
  - `overage_files`
  - `pending_plan_change`
- **Relationships**:
  - Belongs to a merchant/shop
  - Drives the **Review Journey Run** and billing UI states
- **Validation rules**:
  - `overage_files` cannot be negative.
  - Only one active subscription state may exist per `shop_domain`.
  - `declined` or `expired` must produce an actionable blocked-processing state.

## 4. Review Journey Run

- **Purpose**: Tracks one complete reviewer flow from install through product
  verification.
- **Key fields**:
  - `journey_id`
  - `shop_domain`
  - `started_at`
  - `completed_at`
  - `current_stage`
  - `result` — `in_progress`, `passed`, `blocked`, `failed`
  - `blocking_reason`
- **Relationships**:
  - Belongs to a **Review Environment**
  - References one **Merchant Subscription State**
  - Emits many **Approval Gaps** or findings
- **State transitions**:
  - `install_started` -> `authenticated` -> `billing_verified` ->
    `file_uploaded` -> `extraction_reviewed` -> `products_created` ->
    `shopify_verified` -> `passed`
  - Any stage may transition to `blocked` or `failed` with a reason.
- **Validation rules**:
  - `passed` requires all stages to have completed successfully.
  - `blocked` requires a non-empty `blocking_reason`.

## 5. Submission Package

- **Purpose**: Represents the complete set of non-code artifacts required for
  Shopify review.
- **Key fields**:
  - `listing_name`
  - `listing_icon_status`
  - `screenshots_status`
  - `screencast_status`
  - `pricing_details_status`
  - `testing_instructions_status`
  - `support_contact_status`
  - `status` — `draft`, `in_review`, `ready`
- **Relationships**:
  - References one **Review Environment**
  - References one or more **Reviewer Credentials**
  - May have many **Approval Gaps**
- **Validation rules**:
  - `ready` requires all required artifact statuses to be complete and current.
  - Listing pricing and in-app pricing must match exactly before `ready`.

## 6. Approval Gap

- **Purpose**: Captures any blocker or risk that prevents a production-ready,
  review-ready submission.
- **Key fields**:
  - `gap_id`
  - `category` — `policy`, `billing`, `auth`, `security`, `ux`, `operations`,
    `submission`
  - `severity` — `critical`, `major`, `minor`
  - `source` — code audit, test failure, manual review, policy review
  - `description`
  - `owner`
  - `status` — `open`, `in_progress`, `closed`, `accepted_exception`
  - `evidence_link`
- **Relationships**:
  - May be attached to a **Review Environment**, **Review Journey Run**, or
    **Submission Package**
- **Validation rules**:
  - Any `critical` open gap blocks submission readiness.
  - `accepted_exception` requires explicit rationale and time-bound follow-up.
