# Phase 0 Research: Shopify App Store Readiness

## Decision 1: Keep the current embedded app architecture and harden it for review

- **Decision**: Use the existing embedded Shopify Admin architecture with
  `extractor-v3` as the primary reviewer surface and `shopify_supabase_backend`
  as the authenticated API/worker backend.
- **Rationale**: The frontend already uses Shopify App Bridge, embedded routing,
  Prisma-backed session storage, and Shopify billing configuration. Replacing the
  embedded stack would add risk and violate the structural-quality principle.
- **Alternatives considered**:
  - Rebuild the app around a new storefront or standalone admin UI — rejected
    because Shopify review expects a consistent embedded admin experience.
  - Shift more workflow directly into the backend — rejected because reviewers
    must complete the core flow through the app UI.

## Decision 2: Treat the existing production release checklist as the backend readiness baseline

- **Decision**: Use `docs/production-release-checklist.md` as the primary source
  of backend production blockers and close the specific items already identified:
  wildcard CORS, missing health/readiness endpoints, weak production auth/token
  persistence, webhook verification gaps, and incomplete operational evidence.
- **Rationale**: The repository already documents the same blockers that would
  fail Shopify review or create production risk. Using that checklist keeps the
  work grounded in existing backend standards instead of inventing a new release
  framework.
- **Alternatives considered**:
  - Create a new approval-only checklist — rejected because it would duplicate
    existing operational guidance.
  - Ignore backend release blockers until after submission — rejected because
    reviewer-facing flows depend on backend stability and secure tenancy.

## Decision 3: Keep Shopify-managed billing and complete the missing approval-safe flows

- **Decision**: Build on the existing Shopify billing configuration in
  `app/shopify.server.ts`, billing routes in `app/routes/app.billing*.tsx`, and
  backend billing sync routes in `api/agents/billing.py` rather than introducing
  custom billing. The plan must complete billing return/recovery states, usage
  visibility, decline handling, webhook synchronization, and reviewer-friendly
  messaging.
- **Rationale**: Shopify-managed billing is mandatory for approval, and the app
  already has Starter/Growth/Scale plans, tests for billing action behavior, and
  webhook subscription registration.
- **Alternatives considered**:
  - Move billing outside Shopify — rejected because App Store policy forbids it.
  - Remove paid plans for submission — rejected because current product behavior
    and merchant workflows already depend on plan enforcement.

## Decision 4: Keep the frontend-to-backend proxy contract as the trust boundary

- **Decision**: Preserve `app/routes/api.backend-proxy.tsx` as the primary
  frontend-to-backend integration seam, and tighten review-critical behavior
  around tenant scoping, idempotency, error normalization, and upstream recovery.
- **Rationale**: The proxy already enforces contract versioning, authenticated
  tenant context, spoofing protection, and canonical envelopes. This is the
  safest place to keep frontend flows decoupled from backend changes while
  maintaining reviewer-facing consistency.
- **Alternatives considered**:
  - Call backend routes directly from many frontend surfaces — rejected because
    it would duplicate trust-boundary logic and create inconsistent failures.
  - Shift more tenant logic into the browser — rejected because the constitution
    forbids trusting client-only tenancy data.

## Decision 5: Make reviewer experience and submission assets first-class deliverables

- **Decision**: Treat reviewer credentials, test store readiness, sample files,
  screencast, screenshots, pricing/listing parity, and support contact data as
  required deliverables in the same feature, not post-implementation admin work.
- **Rationale**: Shopify review can be blocked even if the code works when the
  listing, credentials, or testing instructions are incomplete. The spec's user
  story 3 depends on these artifacts.
- **Alternatives considered**:
  - Leave submission assets outside the engineering plan — rejected because the
    app would still fail review without them.
  - Create only high-level notes — rejected because the reviewer journey needs a
    precise, repeatable package.

## Decision 6: Limit approval scope to the current core value and explicitly exclude unrelated categories

- **Decision**: Submit against the current embedded admin workflow for document
  upload, extraction, review, and product creation. Payments-app, sales-channel,
  theme-modification, and other category-specific requirements remain out of
  scope unless the product direction changes.
- **Rationale**: The app is not a payments app or sales channel, and it does not
  modify storefront themes as its core feature. Keeping scope tight reduces
  approval risk and keeps the plan aligned to the current product.
- **Alternatives considered**:
  - Add category-specific features for submission polish — rejected because they
    are unrelated to the current reviewer journey and would expand scope.

## Decision 7: Preserve cookie-safe core flows and treat local browser state as optional only

- **Decision**: Ensure the review-critical flow does not depend on local browser
  state. Any `localStorage` usage, such as run-log column preferences, must be
  treated as optional preference storage with graceful fallback, while auth,
  billing, drafts, and review progress remain server-backed.
- **Rationale**: Shopify requires embedded apps to function without relying on
  third-party cookies or fragile local storage behavior. The codebase already
  uses server-side sessions and backend-backed draft/run state for core flows.
- **Alternatives considered**:
  - Ignore local browser state because it is not core — rejected because review
    testing in privacy-restricted sessions can still expose brittle behavior.
  - Move all preference state to the server immediately — rejected because it is
    unnecessary for optional non-critical preferences.

## Decision 8: Use existing automated gates plus a reviewer journey validation layer

- **Decision**: Reuse existing frontend and backend commands as the minimum
  verification layer, then add explicit reviewer-journey validation for install,
  billing, upload, extraction, and submission-package completeness.
- **Rationale**: The repo already exposes useful evidence:
  - Frontend: `npm run lint`, `npm run typecheck`, `npm run test:contract`,
    `npm run build`
  - Backend: `PYTHONPATH=. uv run pytest -q tests/test_release_readiness_gates.py`
    and targeted route/idempotency tests
  These are strong foundations but do not yet prove the full reviewer journey.
- **Alternatives considered**:
  - Depend only on manual review — rejected because the constitution requires
    evidence-driven testing.
  - Add a brand new test stack before closing blockers — rejected because it
    would delay the highest-risk readiness work.
