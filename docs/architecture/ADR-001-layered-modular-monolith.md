# ADR-001: Layered Modular Monolith

## Status
Proposed

## Context
`api/agents/*.py` currently mixes transport concerns with orchestration, persistence, and business rules (run lifecycle updates, Shopify resolution, external service retries). The team is small (<1000 req/day target, sub-$100/mo budget) and needs a reliable path forward without adopting microservices or heavy infrastructure.

## Decision
Adopt a layered modular-monolith with explicit boundaries:

- `api/`: FastAPI routes/controllers responsible only for request validation, response shaping, and error mapping.
- `application/`: Feature-focused use-cases and orchestrators that coordinate persistence, external calls, and domain policies.
- `domain/`: Pure business rules, entities, and policies without external I/O dependencies.
- `infrastructure/`: Implementations of storage, tracing, Collabora, Shopify, and Supabase adapters satisfying ports defined by `application`.
- `shared/`: Shared primitives such as configuration, errors, logging, and tracing helpers.

Dependency direction must remain downward (api → application → domain, infrastructure implements ports). Business logic must live in `application`/`domain` modules while API handlers remain thin.

## Consequences
- Pros: clearer testing matrix (use-case/unit tests), reduced failure blast radius when routes change, and easier reasoning about reliability/security concerns.
- Cons: requires initial refactor effort to move existing logic and define ports.
- Will introduce ADR follow-ons for concrete adapters and orchestration choices (e.g., ADR-002 for use-case structure, ADR-003 for integration resilience).

