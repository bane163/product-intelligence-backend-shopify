# Production Release Checklist

Use this checklist as a go/no-go gate before releasing to production.

## 1) Security (Release Blocker)

### Required checks
- [ ] Backend verifies a signed identity per request (do not trust client-provided `x-shop-domain` alone).
- [ ] CORS is restricted to explicit frontend origins (no wildcard origin with credentials).
- [ ] WOPI access tokens are signed, short-lived, and scoped (`file_id`, `shop_domain`, `scope`, `exp`).
- [ ] Collabora is locked down to explicit allowed domains/frame ancestors (no `domain=.*` or `*` framing policy).
- [ ] Secrets are in a secrets manager/KMS (no plaintext token JSON files in runtime).
- [ ] Supabase service-role key is backend/worker only and never exposed to browser clients.

### Fail conditions (do not release)
- Any endpoint authorizes tenancy from user-controlled headers or form fields.
- Any static or guessable WOPI token model (for example: `"edit"` / `"view"`).
- Any production secret committed to repo, image, or plaintext runtime file.

---

## 2) Reliability & Scalability

### Required checks
- [ ] Production deploy is immutable (no `--reload`, no source bind-mounts).
- [ ] Frontend session storage uses managed Postgres (not local SQLite for production).
- [x] Worker queue has retry/backoff and dead-letter handling.
- [ ] Health and readiness probes exist for frontend, backend, worker, and Collabora.
- [ ] Long-lived connections (SSE/WebSocket) are configured for edge/proxy timeouts.

### Fail conditions (do not release)
- Single-instance local state required for normal operation.
- Retry policy for async/offload jobs is disabled, bypassed, or not exercised in staging.
- No readiness check to detect partial outages.

---

## 3) Observability & Operations

### Required checks
- [ ] Structured logs with correlation IDs across frontend -> backend -> worker.
- [ ] Metrics and alerts for API error rate, queue backlog, worker failures, and Collabora health.
- [ ] Incident runbook includes rollback steps and service ownership.
- [ ] Backup/restore drill performed and documented (Supabase DB + storage recovery path verified).

### Fail conditions (do not release)
- No alerting on critical failure modes.
- No tested restore path.

---

## 4) Recommended Deployment Topology

### Public edge
- **Cloudflare** fronts the frontend app domain.

### Private services
- **Backend API (FastAPI)**: private origin, reachable by frontend server and approved internal paths.
- **Worker**: private, no public ingress.
- **Collabora**: private/internal service, only exposed through controlled hostname/routing when needed.

### Data layer
- **Supabase**: separate projects for `dev`, `staging`, `prod`, with strict RLS and service-role isolation.

### Release pipeline
- [ ] CI validates lint/test/build for frontend and backend.
- [ ] Migration gate and rollback plan are defined.
- [ ] Environment promotion is `dev -> staging -> prod`.

---

## 5) Cloudflare + Collabora Integration Pattern

- [ ] Frontend served through Cloudflare.
- [ ] Collabora served on dedicated hostname (for example `office.<domain>`), with WebSocket support enabled.
- [ ] Collabora can call backend WOPI endpoints through controlled routes only.
- [ ] WOPI endpoints validate signed token claims and shop tenancy.
- [ ] Cloudflare caching/buffering rules are set to preserve WOPI/SSE behavior.

---

## 6) Supabase + Python Backend Contract

- [ ] Backend owns privileged writes with service-role key.
- [ ] Frontend accesses sensitive flows through authenticated backend routes.
- [ ] Any direct client reads are protected by strict tenant RLS and scoped JWT claims.
- [ ] Backend rejects unsigned or unverified tenant context.

---

## 7) Final Go/No-Go Decision

Mark release as **GO** only if all sections above pass with no open blockers.

- Security: PASS / FAIL
- Reliability & Scalability: PASS / FAIL
- Observability & Operations: PASS / FAIL
- Deployment Topology Readiness: PASS / FAIL
- Cloudflare + Collabora Integration: PASS / FAIL
- Supabase + Backend Contract: PASS / FAIL

**Overall release decision:** GO / NO-GO

---

## 8) Release Readiness Gates (Roadmap item #5)

### Contract + flow gate evidence
- [x] Frontend proxy contract tests pass (`cd ../extractor-v3 && npm run -s test:contract`).
- [x] Backend app-store readiness gates pass (`PYTHONPATH=. uv run pytest -q tests/test_app_store_readiness.py`).
- [x] Default backend suite is offline and deterministic (`PYTHONPATH=. uv run pytest -q -m "not integration"`).
- [x] Live-infrastructure tests have an opt-in `integration` marker and manual CI job.
- [x] Backend intelligence release gates pass (`PYTHONPATH=. uv run pytest -q tests/test_release_readiness_gates.py`).
- [x] Backend intelligence route spot-checks pass (`PYTHONPATH=. uv run pytest -q tests/test_agents_routes.py -k "test_run_and_get_product_intelligence_audit or test_apply_bulk_with_idempotency_key_replays_without_reapplying or test_apply_bulk_idempotency_key_conflict_on_payload_mismatch"`).
- [ ] Critical intelligence path is green: audit -> suggestions -> apply -> revert.
- [ ] Apply-bulk idempotency replay/conflict assertions are green.

### Tenant isolation + rollback safety evidence
- [ ] Tenant negative-path checks are green for intelligence endpoints (missing/mismatched tenant context).
- [ ] Migration/rollback plan is documented for release batch (including owner and rollback trigger criteria).
- [ ] Release artifact includes command output links/logs for all gate checks above.
- [ ] Reviewer dry-run evidence is captured in `specs/001-app-store-readiness/submission/reviewer-dry-run.md`.
- [ ] Submission workspace artifacts are current in `specs/001-app-store-readiness/submission/`.

### Fail conditions (do not release)
- Any contract gate command fails or is skipped.
- Rollback trigger/owner is undefined for the release batch.
