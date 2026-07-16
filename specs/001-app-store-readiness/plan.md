# Implementation Plan: Shopify App Store Readiness

**Branch**: `001-app-store-readiness` | **Date**: 2026-04-11 | **Spec**: `/Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/specs/001-app-store-readiness/spec.md`
**Input**: Feature specification from `/specs/001-app-store-readiness/spec.md`

## Summary

Prepare Stockpile for Shopify App Store submission by hardening the embedded
install/auth/billing path, eliminating production blockers in the backend and
frontend, and producing the submission package a reviewer needs to complete the
core upload-to-product workflow without friction. The plan uses the existing
React Router embedded app, Shopify billing configuration, backend release gates,
and Supabase-backed workflows instead of inventing parallel systems.

## Technical Context

**Language/Version**: Python 3.13 backend; TypeScript 5.9 / React Router 7 frontend  
**Primary Dependencies**: FastAPI, Supabase, Shopify App React Router, App Bridge, Prisma session storage, Vitest, pytest  
**Storage**: Supabase Postgres/storage for app data; Prisma-managed session database for the embedded app  
**Testing**: `pytest` + `pytest-asyncio` for backend; `vitest`, ESLint, and TypeScript typecheck for frontend  
**Target Platform**: Embedded Shopify Admin app with a separate FastAPI API and background worker  
**Project Type**: Two-repository web application (`shopify_supabase_backend` + `extractor-v3`)  
**Performance Goals**: Install approval to main workspace in under 2 minutes; standard upload-to-product flow in under 10 minutes; no blocking web errors on review-critical flows; visible progress for long-running work  
**Constraints**: Must satisfy Shopify App Store requirements, use Shopify-managed billing, keep core flows usable without third-party cookies, preserve embedded-app-safe navigation, tighten CORS/TLS/tenant controls, and maintain truthful listing/submission assets  
**Scale/Scope**: Public app review readiness across auth, billing, embedded UX, backend production safety, reviewer materials, and release evidence for both repos

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Structural Code Quality**: PASS. Backend changes stay in the current seams:
  `main.py`, `auth.py`, `api/agents/*`, `application/*`, and supporting service
  layers. Frontend changes stay within `app/routes/*`, `app/features/billing/*`,
  `app/components/*`, `app/utils/*`, and `app/shopify.server.ts`. No new repo,
  service, or cross-layer shortcut is required.
- **Evidence-Driven Testing**: PASS with explicit required evidence. Backend
  verification will extend or reuse `tests/test_release_readiness_gates.py`,
  billing/API route tests, and relevant agent route tests. Frontend verification
  will extend route/contract/billing tests and add reviewer-journey, billing
  edge-case, and incognito/accessibility coverage where behavior changes.
- **Shopify-Native Experience Consistency**: PASS if all touched reviewer-facing
  surfaces preserve embedded-app-safe transitions, App Bridge expectations, and
  explicit loading/empty/error/success states. Billing, onboarding, and recovery
  messaging must remain merchant-friendly and aligned with Shopify review
  expectations.
- **Performance & Reliability Budgets**: PASS with the documented goals above.
  Design artifacts define install, workflow, background-progress, and degraded
  behavior expectations, and the implementation will use existing release-gate
  evidence plus new reviewer-flow checks.
- **Observability, Security & Tenant Isolation**: PASS only if implementation
  closes the known blockers: wildcard CORS, weak production auth state/token
  storage, missing health/readiness checks, insufficient webhook hardening, and
  any tenant verification gaps. These blockers are captured directly in
  `research.md` and must remain in scope.

**Post-design re-check**: PASS. Phase 0 research resolved planning unknowns
without introducing new architectural exceptions, and Phase 1 artifacts keep the
feature within the current frontend/backend seams while preserving the required
testing, UX, performance, and security gates.

## Project Structure

### Documentation (this feature)

```text
specs/001-app-store-readiness/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── reviewer-journey.md
│   └── submission-package.md
└── tasks.md
```

### Source Code (repository root)

```text
shopify_supabase_backend/
├── api/
│   └── agents/
├── application/
├── auth.py
├── main.py
├── services/
├── shared/
└── tests/

extractor-v3/
├── app/
│   ├── components/
│   ├── config/
│   ├── features/
│   ├── routes/
│   ├── utils/
│   └── shopify.server.ts
├── prisma/
├── shopify.app.toml
└── app/**/*.test.ts
```

**Structure Decision**: Keep the existing split frontend/backend product
structure. Planning and implementation must coordinate changes across both repos
instead of collapsing the app into a single project or bypassing the current
frontend-to-backend contract boundary.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None currently justified | N/A | The existing repo split, billing architecture, and proxy boundary are sufficient for this feature |
