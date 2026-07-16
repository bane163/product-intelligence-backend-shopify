# Quickstart: Validate Shopify App Store Readiness

## Prerequisites

- Backend environment variables configured for a non-local reviewable deployment
- Frontend environment variables configured for the same review environment
- Review store and reviewer access plan prepared
- A sample upload file available for the scripted reviewer journey

## 1. Verify backend readiness

```bash
cd /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend
PYTHONPATH=. uv run pytest -q tests/test_app_store_readiness.py
PYTHONPATH=. uv run pytest -q tests/test_release_readiness_gates.py
PYTHONPATH=. uv run pytest -q tests/test_agents_routes.py -k "test_run_and_get_product_intelligence_audit or test_apply_bulk_with_idempotency_key_replays_without_reapplying or test_apply_bulk_idempotency_key_conflict_on_payload_mismatch"
```

Expected outcome:
- App-store readiness endpoints and CORS expectations pass
- Release-readiness gates pass
- Critical intelligence and idempotency route checks pass

## 2. Verify frontend readiness

```bash
cd /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3
npm run lint
npm run typecheck
npm run test:contract
npm run build
```

Expected outcome:
- No lint/type/build blockers
- Frontend/backend proxy contract remains valid

## 3. Run the reviewer journey

1. Install the app from Shopify into the review store.
2. Approve access and confirm the app opens inside Shopify Admin.
3. If billing is required, choose a plan and confirm the return flow lands back
   in the app with a clear state.
4. Upload the sample file and confirm progress is visible through extraction and
   review.
5. Create products and verify the resulting products in Shopify Admin.

Expected outcome:
- No blocking web errors
- Billing, workflow, and recovery states are understandable
- App output matches Shopify results

Capture timing and outcome evidence in:

- `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/reviewer-dry-run.md`

Suggested timing checkpoints:

1. Install approval -> embedded app workspace visible
2. Billing selection -> return to app with clear status
3. Sample upload -> extraction review ready
4. Product submission -> products visible in Shopify Admin

## 4. Validate the submission package

Confirm that the following are current and aligned with the live app:

1. Listing name and icon
2. Pricing details
3. Screenshots
4. Screencast
5. Reviewer access instructions and secure credential handoff
6. Testing instructions
7. Support and emergency contact data

Also confirm the following metadata surfaces agree before upload:

1. `extractor-v3/shopify.app.toml`
2. `extractor-v3/README.md`
3. `shopify_supabase_backend/README.md`
4. In-app billing configuration in `extractor-v3/app/config/billing.ts`

Capture evidence in:

- `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/reviewer-dry-run.md`
- `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/reviewer-access.md`
- `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/listing-copy.md`
- `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/screencast-script.md`
- `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/screenshot-shotlist.md`
- `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/submission-checklist.md`

Expected outcome:
- Reviewer can start and complete review without requesting missing materials
- Every placeholder marked `REPLACE_BEFORE_SUBMISSION` is resolved

## 5. Audit unresolved submission placeholders

```bash
cd /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend
rg -n "REPLACE_BEFORE_SUBMISSION" specs/001-app-store-readiness/submission
```

Expected outcome:
- The command returns no matches before the final Shopify submission upload
