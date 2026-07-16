---

description: "Task list for Shopify App Store readiness completion"
---

# Tasks: Shopify App Store Readiness

**Input**: Design documents from `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/`  
**Prerequisites**: `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/plan.md`, `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/spec.md`, `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/research.md`, `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/data-model.md`, `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/contracts/reviewer-journey.md`, `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/contracts/submission-package.md`, `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/quickstart.md`

**Tests**: Tests are required for changed reviewer-critical behavior, trust-boundary routes, billing states, recovery behavior, and Product Intelligence UX defaults. Documentation-only tasks in the submission package do not require automated tests.

**Organization**: Tasks are grouped by user story so each story remains independently testable and shippable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on unfinished tasks)
- **[Story]**: Story label for user-story phases only (`[US1]`, `[US2]`, `[US3]`)
- Every task includes the exact file path to touch

## Phase 1: Setup (Shared Execution Inputs)

**Purpose**: Lock the remaining review environment inputs, evidence destinations, and submission sources before story-specific work.

- [ ] T001 Audit unresolved review-environment and submission placeholders in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/README.md` and `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/review-environment.md`
- [ ] T002 [P] Refresh verification instructions and evidence destinations in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/quickstart.md` and `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/docs/production-release-checklist.md`
- [ ] T003 [P] Confirm live app metadata sources in `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/shopify.app.toml`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/config/billing.ts`, and `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/listing-copy.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Keep the readiness safety nets current before final reviewer-flow and merchant-trust changes.

**⚠️ CRITICAL**: No user story should be signed off until this phase is complete.

- [ ] T004 Preserve backend release-readiness coverage in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/tests/test_app_store_readiness.py` and `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/tests/test_release_readiness_gates.py`
- [ ] T005 [P] Preserve install, auth, and backend-proxy contract coverage in `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/api.backend-proxy.contract.test.ts`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/webhooks.app.installed.test.ts`, and `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/auth.login/error.server.test.ts`
- [ ] T006 [P] Preserve Documents and run-details regression coverage in `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/features/home/services/homeFilesApi.test.ts`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.documents.route-boundary.test.ts`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/api.run-logs.$runId.test.ts`, and `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/utils/run-details-presenters.test.ts`

**Checkpoint**: Shared trust-boundary and release-readiness protections are current, so story work can proceed safely.

---

## Phase 3: User Story 1 - Complete Review Without Blockers (Priority: P1) 🎯 MVP

**Goal**: Let a Shopify reviewer install the app, reach the embedded workspace, use Product Source Files successfully, and complete the upload-to-product workflow without blocking errors.

**Independent Test**: In a clean review store, a reviewer installs the app, reaches the Product Source Files surface, loads Uploaded Files, uploads a supported source file, reviews extraction output, and creates products without broken navigation or raw error pages.

### Tests for User Story 1 ⚠️

- [ ] T007 [P] [US1] Extend reviewer-journey and embedded-workspace coverage in `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/__reviewer-journey__.test.tsx` and `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.nav.test.ts`
- [ ] T008 [P] [US1] Extend Product Source Files tab, processing, and action coverage in `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/utils/documents-tabs.test.ts`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/utils/documents-processing.test.ts`, and `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/utils/documents-actions.test.ts`
- [ ] T009 [P] [US1] Extend backend workflow and run retrieval coverage in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/tests/test_agents_routes.py` and `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/tests/test_runs_routes.py`
- [x] T027 [P] [US1] Add simulator-backed upload/import access regressions in `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/api.backend-proxy.contract.test.ts`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/api.dev-billing-simulator.test.ts`, and `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/tests/test_agents_routes.py`

### Implementation for User Story 1

- [ ] T010 [US1] Rename the Documents surface to Product Source Files and add the merchant-facing purpose blurb in `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/documents-page.tsx`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.documents._index.tsx`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/i18n/translations/en.json`, and `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/features/billing/navigation.ts`
- [ ] T011 [US1] Harden Product Source Files loading, empty, and recovery states around the proxy-backed file workflow in `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/hooks/useFiles.ts`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/features/home/services/homeFilesApi.ts`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/components/ErrorRecovery.tsx`, and `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/components/StateRenderer.tsx`
- [ ] T012 [US1] Validate the live install-to-upload reviewer journey and record timing plus outcome evidence in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/reviewer-dry-run.md` and `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/quickstart.md`
- [x] T028 [US1] Propagate enabled billing simulator state through `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/api.backend-proxy.tsx`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/features/billing/dev-billing-simulator.ts`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/features/billing/dev-billing-simulator.server.ts`, `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/api/agents/utils.py`, `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/api/agents/files.py`, and `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/api/agents/billing.py`

**Checkpoint**: The reviewer can reach the core workflow, Product Source Files is clear and stable, and the review path no longer blocks on the document surface.

---

## Phase 4: User Story 2 - Trustworthy Merchant Experience (Priority: P2)

**Goal**: Keep pricing, billing, run details, and Product Intelligence behavior transparent, privacy-safe, and understandable without support intervention.

**Independent Test**: A merchant can choose or change plans, return from Shopify billing into a clear state, avoid seeing sensitive run message history in deployed environments, and land in Product Intelligence with a clear detail hierarchy and alphabetical default ordering.

### Tests for User Story 2 ⚠️

- [ ] T013 [P] [US2] Maintain billing return-state and usage coverage in `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.billing.plans.test.tsx`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.billing.plans.action.test.ts`, and `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.billing._index.test.tsx`
- [ ] T014 [P] [US2] Add deployed-environment run-history redaction coverage in `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/api.run-logs.$runId.test.ts` and `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/utils/run-details-presenters.test.ts`
- [ ] T015 [P] [US2] Add Product Intelligence default-sort, detail-header, and embedded-navigation coverage in `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/features/data-intelligence/hooks/useDataIntelligenceGrid.test.ts`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/features/billing/navigation.test.ts`, and `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.nav.test.ts`
- [x] T029 [P] [US2] Add simulator-active subscription and apostrophe-rendering coverage in `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/features/billing/TrialBanner.test.tsx`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.billing._index.test.tsx`, and `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.settings.billing-simulator.test.ts`

### Implementation for User Story 2

- [ ] T016 [US2] Gate run-details message history to local development only in `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/api.run-logs.$runId.tsx`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.run-logs_.$runId.tsx`, and `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/i18n/translations/en.json`
- [ ] T017 [US2] Increase Product Intelligence detail-header prominence and switch the default grid sort to title ascending in `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/features/data-intelligence/hooks/useDataIntelligenceGrid.ts`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.data-intelligence_.products.$productId.tsx`, and `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/i18n/translations/en.json`
- [ ] T018 [US2] Validate live billing, plan-change, and merchant-trust messaging outcomes in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/reviewer-dry-run.md`, `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/listing-copy.md`, and `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/quickstart.md`
- [x] T030 [US2] Treat enabled billing simulator mode as an active subscription and replace literal `&apos;` copy in `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/features/billing/BillingSimulatorCard.tsx`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.tsx`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/features/billing/TrialBanner.tsx`, `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.billing._index.tsx`, and `/Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/i18n/translations/en.json`

**Checkpoint**: Billing and Product Intelligence surfaces are transparent, safe, and reviewer-friendly without exposing internal data.

---

## Phase 5: User Story 3 - Submission Package Ready for Review (Priority: P3)

**Goal**: Finish the non-code deliverables so Shopify can review the app without missing credentials, stale instructions, or unresolved submission gaps.

**Independent Test**: A non-developer reviewer can follow the package, sign in with the provided credentials, understand the app value and pricing, and complete the documented review flow without needing extra clarification.

### Implementation for User Story 3

- [ ] T019 [P] [US3] Fill live review-environment and listing metadata in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/review-environment.md` and `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/listing-copy.md`
- [ ] T020 [P] [US3] Fill reviewer credential, contact, and sample-file handoff details in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/reviewer-access.md` and `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/README.md`
- [ ] T021 [P] [US3] Capture screenshot ownership and screencast execution details in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/screenshot-shotlist.md` and `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/screencast-script.md`
- [ ] T022 [US3] Finalize submission readiness criteria and reviewer instructions in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/submission-checklist.md` and `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/reviewer-dry-run.md`

**Checkpoint**: The submission package is complete enough for Shopify to begin review without avoidable back-and-forth.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Run final verification, placeholder audits, and end-to-end evidence capture across all stories.

- [ ] T023 [P] Run backend verification commands defined in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/quickstart.md` and record results in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/reviewer-dry-run.md`
- [ ] T024 [P] Run frontend verification commands defined in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/quickstart.md` and record results in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/reviewer-dry-run.md`
- [ ] T025 Run the submission placeholder audit for `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/` and reconcile findings in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/README.md` and `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/submission-checklist.md`
- [ ] T026 Conduct the final reviewer dry run and reconcile any last mismatches in `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/reviewer-dry-run.md`, `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/review-environment.md`, and `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/submission-checklist.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1: Setup** — No dependencies; establish the live-input sources first
- **Phase 2: Foundational** — Depends on Phase 1 and blocks sign-off for all user stories
- **Phase 3: US1** — Depends on Phase 2 and forms the reviewable MVP
- **Phase 4: US2** — Depends on Phase 2 and is safest after US1 proves the reviewer path is stable
- **Phase 5: US3** — Depends on Phase 2, but final content depends on confirmed live outputs from US1 and US2
- **Phase 6: Polish** — Depends on all desired user stories being complete

### User Story Dependencies

- **US1 (P1)**: Starts immediately after Foundational and establishes the reviewable install-to-product path
- **US2 (P2)**: Starts after Foundational and can overlap once US1 has stabilized the embedded reviewer journey
- **US3 (P3)**: Starts after Foundational, but final placeholder replacement should happen after live behavior and reviewer evidence are confirmed

### Within Each User Story

- Write or update readiness-critical tests before closing behavior changes
- Keep trust-boundary and reviewer-facing copy changes paired with regression coverage
- Record live evidence only after the corresponding behavior is stable in the deployed app

### Parallel Opportunities

- T002 and T003 can run in parallel during Setup
- T005 and T006 can run in parallel during Foundational
- T007, T008, and T009 can run in parallel within US1
- T007, T008, T009, and T027 can run in parallel within US1
- T013, T014, T015, and T029 can run in parallel within US2
- T019, T020, and T021 can run in parallel within US3
- T023 and T024 can run in parallel during Polish

---

## Parallel Example: User Story 1

```bash
Task: "Extend reviewer-journey and embedded-workspace coverage in /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/__reviewer-journey__.test.tsx and /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.nav.test.ts"
Task: "Extend Product Source Files tab, processing, and action coverage in /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/utils/documents-tabs.test.ts, /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/utils/documents-processing.test.ts, and /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/utils/documents-actions.test.ts"
Task: "Extend backend workflow and run retrieval coverage in /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/tests/test_agents_routes.py and /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/tests/test_runs_routes.py"
Task: "Add simulator-backed upload/import access regressions in /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/api.backend-proxy.contract.test.ts, /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/api.dev-billing-simulator.test.ts, and /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/tests/test_agents_routes.py"
```

## Parallel Example: User Story 2

```bash
Task: "Maintain billing return-state and usage coverage in /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.billing.plans.test.tsx, /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.billing.plans.action.test.ts, and /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.billing._index.test.tsx"
Task: "Add deployed-environment run-history redaction coverage in /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/api.run-logs.$runId.test.ts and /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/utils/run-details-presenters.test.ts"
Task: "Add Product Intelligence default-sort, detail-header, and embedded-navigation coverage in /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/features/data-intelligence/hooks/useDataIntelligenceGrid.test.ts, /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/features/billing/navigation.test.ts, and /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.nav.test.ts"
Task: "Add simulator-active subscription and apostrophe-rendering coverage in /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/features/billing/TrialBanner.test.tsx, /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.billing._index.test.tsx, and /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/app/routes/app.settings.billing-simulator.test.ts"
```

## Parallel Example: User Story 3

```bash
Task: "Fill live review-environment and listing metadata in /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/review-environment.md and /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/listing-copy.md"
Task: "Fill reviewer credential, contact, and sample-file handoff details in /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/reviewer-access.md and /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/README.md"
Task: "Capture screenshot ownership and screencast execution details in /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/screenshot-shotlist.md and /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/submission/screencast-script.md"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: User Story 1
4. Validate the live Product Source Files and upload-to-product reviewer flow
5. Stop and review the evidence before moving to billing trust or submission packaging

### Incremental Delivery

1. Complete Setup + Foundational
2. Close US1 and validate the live reviewer journey
3. Close US2 and validate billing trust plus privacy-safe embedded behavior
4. Close US3 and remove all submission-package gaps
5. Run the Polish phase and final dry run before submission

### Parallel Team Strategy

1. One owner confirms live review-environment inputs while another keeps regression coverage current
2. After Foundational, one engineer can stabilize the reviewer journey while another handles billing trust and Product Intelligence refinements
3. Once live behavior is confirmed, a documentation owner can finish the submission package in parallel

---

## Notes

- Total tasks: 26
- Task count by story: US1 = 6, US2 = 6, US3 = 4
- Parallelizable tasks: 14
- Independent tests:
  - **US1**: Reviewer installs, reaches Product Source Files, uploads a file, reviews extraction output, and creates products without blockers
  - **US2**: Merchant manages billing, avoids exposed run history in deployed environments, and sees Product Intelligence land in an understandable default state
  - **US3**: Reviewer uses the package materials to access and evaluate the app without extra clarification
- Suggested MVP scope: Phase 1, Phase 2, and Phase 3 (US1)
- Format validation: Every task uses the required checkbox, task ID, optional `[P]`, required story label for user-story phases, and explicit file paths
