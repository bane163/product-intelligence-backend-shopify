# Reviewer Dry Run

## Current status

- **Automated verification status**: pass on 2026-04-11
- **Manual review-store rehearsal**: `REPLACE_BEFORE_SUBMISSION`
- **Recorded by**: `REPLACE_BEFORE_SUBMISSION`
- **Review date**: `REPLACE_BEFORE_SUBMISSION`

## Automated checks

Record the latest command output references here after each submission rehearsal.

| Check | Command | Result | Notes |
| --- | --- | --- | --- |
| Backend readiness | `PYTHONPATH=. uv run pytest -q tests/test_app_store_readiness.py` | `PASS` | 3 passed |
| Backend release gates | `PYTHONPATH=. uv run pytest -q tests/test_release_readiness_gates.py` | `PASS` | 3 passed, 2 Supabase deprecation warnings |
| Backend route spot-checks | `PYTHONPATH=. uv run pytest -q tests/test_agents_routes.py -k "test_run_and_get_product_intelligence_audit or test_apply_bulk_with_idempotency_key_replays_without_reapplying or test_apply_bulk_idempotency_key_conflict_on_payload_mismatch"` | `PASS` | 3 passed, 72 deselected, 2 Supabase deprecation warnings |
| Frontend lint | `npm run lint` | `PASS` | Includes existing TypeScript support warning from `@typescript-eslint/typescript-estree` |
| Frontend typecheck | `npm run typecheck` | `PASS` | React Router typegen + `tsc --noEmit` |
| Frontend contract tests | `npm run test:contract` | `PASS` | 11 passed |
| Frontend build | `npm run build` | `PASS` | Production client + SSR bundles built successfully |

## Manual reviewer journey timing

| Step | Target | Actual | Notes |
| --- | --- | --- | --- |
| Install approval -> app workspace | under 2 minutes | `REPLACE_BEFORE_SUBMISSION` | |
| Plan selection -> return to app | reviewer-safe state | `REPLACE_BEFORE_SUBMISSION` | |
| Upload -> extraction review | understandable progress | `REPLACE_BEFORE_SUBMISSION` | |
| Submit -> product visible in Shopify | under 10 minutes total workflow | `REPLACE_BEFORE_SUBMISSION` | |

## Manual observations

- **Install/auth result**: `REPLACE_BEFORE_SUBMISSION`
- **Billing result**: `REPLACE_BEFORE_SUBMISSION`
- **Upload and extraction result**: `REPLACE_BEFORE_SUBMISSION`
- **Product creation result**: `REPLACE_BEFORE_SUBMISSION`
- **Recovery-state result**: `REPLACE_BEFORE_SUBMISSION`

## Blockers

- Replace all remaining `REPLACE_BEFORE_SUBMISSION` values before sending the
  package to Shopify.
- Capture screenshots, screencast, and manual reviewer timing in the deployed
  review environment.
