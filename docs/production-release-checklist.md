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
- [ ] Worker queue has retry/backoff and dead-letter handling.
- [ ] Health and readiness probes exist for frontend, backend, worker, and Collabora.
- [ ] Long-lived connections (SSE/WebSocket) are configured for edge/proxy timeouts.

### Fail conditions (do not release)
- Single-instance local state required for normal operation.
- No retry policy for async/offload jobs.
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
