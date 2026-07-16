<!--
Sync Impact Report
Version change: template -> 1.0.0
Modified principles:
- template principle slot 1 -> I. Structural Code Quality
- template principle slot 2 -> II. Evidence-Driven Testing
- template principle slot 3 -> III. Shopify-Native Experience Consistency
- template principle slot 4 -> IV. Performance & Reliability Budgets
- template principle slot 5 -> V. Observability, Security & Tenant Isolation
Added sections:
- Engineering Standards
- Delivery Workflow & Quality Gates
Removed sections:
- None
Templates requiring updates:
- ✅ /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/.specify/templates/plan-template.md
- ✅ /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/.specify/templates/spec-template.md
- ✅ /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend/.specify/templates/tasks-template.md
- ✅ /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/.specify/templates/plan-template.md
- ✅ /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/.specify/templates/spec-template.md
- ✅ /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3/.specify/templates/tasks-template.md
- ⚠ pending: No `.specify/templates/commands/` directory exists in either repo, so no command files required updates.
Follow-up TODOs:
- None
-->
# Supa Shop AI Constitution

## Core Principles

### I. Structural Code Quality
All changes MUST preserve explicit structure, not just pass local checks. Backend
work MUST keep request handling thin in `api/`, business rules in
`application`/`domain`, and external integrations behind ports or adapters.
Frontend work MUST preserve feature ownership, typed boundaries, and
design-system-first composition instead of ad hoc UI or cross-feature coupling.
Any intentional boundary violation MUST be documented in the plan, justified in
review, and tracked for removal because structural drift becomes operational
risk.

### II. Evidence-Driven Testing
Every change MUST prove the behavior it modifies at the highest-value test
level. Backend changes affecting domain logic, tenancy, idempotency, async
workers, or API contracts MUST add or update pytest coverage at the relevant
unit, integration, or release-gate layer. Frontend changes affecting merchant
flows, route actions, backend contracts, or shared state MUST add or update the
relevant Vitest contract, route, or component logic coverage. A change that
alters behavior without updating the evidence for that behavior is incomplete,
because regression risk is otherwise invisible.

### III. Shopify-Native Experience Consistency
User-facing work MUST feel native to Shopify Admin across both repos. Frontend
changes MUST use Polaris Web Components first, preserve embedded-app-safe
navigation, keep loading/empty/error/success states explicit, and maintain
accessibility for keyboard, semantic, and responsive use. Backend responses MUST
remain predictable, actionable, and state-friendly so the frontend can render
those merchant states consistently. UX consistency is a product requirement, not
styling preference.

### IV. Performance & Reliability Budgets
Performance and reliability MUST be specified and defended for every affected
critical path. Backend plans MUST define measurable expectations for latency,
throughput, retries, queue/worker behavior, payload size, or failure recovery
when those concerns are in scope. Frontend plans MUST define measurable
expectations for initial rendering, interaction responsiveness, data loading, and
degraded-state behavior when user-facing paths are affected. Long-running,
background, or failure-prone workflows MUST be observable and recoverable by
design because "eventually works" is not production quality.

### V. Observability, Security & Tenant Isolation
Observability and security defaults are mandatory and MUST not be relaxed by
convenience. Correlation IDs, structured logs, explicit error surfaces,
idempotency for replay-sensitive mutations, signed or authenticated access
patterns, and secret handling that never trusts the browser are required
defaults. Multi-tenant behavior MUST be verified at the backend boundary and
preserved through storage, jobs, and API responses; client-supplied tenancy data
alone is never sufficient. Production behavior MUST not depend on local mutable
state that cannot survive retries, restarts, or concurrent execution.

## Engineering Standards

- **Backend architecture**: `api` MAY orchestrate request/response concerns but
  MUST delegate business behavior to `application` and `domain` layers.
  Infrastructure concerns MUST remain replaceable through ports, adapters, or
  similarly explicit seams.
- **Frontend architecture**: route files, feature modules, shared components, and
  service utilities MUST retain clear ownership boundaries. New UI work MUST use
  Polaris Web Components first and MUST NOT introduce new deprecated Polaris
  React surfaces.
- **Contracts and states**: when backend responses drive frontend flows, both
  sides MUST preserve explicit contract shape, loading/error semantics, and safe
  fallbacks for missing or delayed data.
- **Performance expectations**: plans and specs MUST state measurable budgets for
  the affected path instead of vague goals such as "fast" or "optimized."
- **Operational resilience**: queues, retries, realtime updates, and long-lived
  workflows MUST describe recovery behavior, timeout assumptions, and failure
  ownership.

## Delivery Workflow & Quality Gates

- Every plan MUST include a constitution check covering structure, testing, UX
  consistency, performance/reliability, and observability/security/tenancy.
- Every spec MUST describe the affected user journeys, required test evidence,
  UX or API state expectations, and measurable success criteria for affected
  performance or reliability paths.
- Every task list MUST include the concrete verification work implied by the
  change. Tests are required whenever behavior, contracts, accessibility, tenant
  isolation, observability, or performance expectations change.
- Reviews MUST reject changes that bypass architectural seams, weaken automated
  coverage for changed behavior, degrade merchant-state consistency, or remove
  observability/security controls without an approved amendment.
- Release-facing work MUST re-check constitution compliance before merge and
  before production rollout for the affected surfaces.

## Governance

- This constitution applies to the Supa Shop AI product across both repositories:
  `shopify_supabase_backend` and `extractor-v3`. When local guidance conflicts,
  the stricter rule wins unless this constitution is amended.
- Amendments MUST update both constitution copies and any affected `.specify`
  templates in the same change set so governance and planning artifacts do not
  drift.
- Semantic versioning governs this document. MAJOR increments cover removed or
  incompatible principles, MINOR increments cover new principles or materially
  expanded governance, and PATCH increments cover clarifications that do not
  change expected behavior.
- Compliance review is mandatory at three points: planning, implementation
  review, and pre-release validation for affected production surfaces.
- If a change cannot meet a principle immediately, the deviation MUST be named,
  justified, time-bounded, and tracked in the relevant plan or review record.

**Version**: 1.0.0 | **Ratified**: 2026-04-11 | **Last Amended**: 2026-04-11
