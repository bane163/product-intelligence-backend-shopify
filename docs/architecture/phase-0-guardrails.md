# Phase 0: Guardrails for Layered Refactor

## Purpose
Provide concrete, enforceable guardrails for the layered modular-monolith refactor so API route modules remain thin and business logic is moved into `application` and `domain` layers.

## Import and dependency rules
- Only these imports are allowed in `api/` modules:
  - FastAPI framework primitives (fastapi, fastapi.responses)
  - Pydantic/DTO classes for request/response bodies
  - Thin adapters from `application` layer (e.g., `from application.use_cases import process_document`)
  - Shared error types (from `shared.errors`) and AppContext factory function
- `api/` MUST NOT directly call infrastructure service methods like `ctx.services.supabase.*` or `ctx.services.llm.*` except for very small legacy shim methods during migration (add a TODO comment when used).
- `application/` can import `domain` and `shared`; must not import `api`.
- `infrastructure/` implements ports/interfaces expected by `application`; these adapters may import 3rd-party SDKs.

## Naming conventions
- Use-case modules: `application/use_cases/<feature>.py` and provide a single entrypoint `execute(...)` or `run(...)`.
- Domain modules: `domain/<concept>.py` with pure business functions and entities.
- Adapter modules: `infrastructure/<system>_adapter.py` implementing a small interface (e.g., `save_file`, `get_file`, `create_product_from_input`).

## API error mapping
- Use-case/domain errors should raise domain-specific exceptions (e.g., `DomainError`, `NotFoundError`, `ValidationError`).
- API controllers catch these and convert to HTTP exceptions with proper status codes.
- All exceptions must include a correlation id when sent to logs/clients.

## Observability and tracing
- Inject a correlation id at the API boundary and pass through to use-cases and adapters.
- Use `trace_event` or a similar run emitter abstraction for tracing long-running flows.

## Migration checklist
1. Add one use-case module under `application/use_cases` for the target flow.
2. Replace heavy logic in route with a thin call to the use-case `execute(...)` and map the result to a response.
3. Add unit tests for the use-case (mocking ports) and minimal route tests verifying wiring.
4. Remove direct references to `ctx.services.*` from the route once the use-case handles them.

## High-impact extraction candidates (recommended order)
1. `process_excel` (route: `/agents/import` or `/agents/excel`) — currently coordinates run lifecycle and calls `ctx.services.llm.run_excel_agent_workflow`.
2. `submit_products_to_shopify` (route: `/agents/submit-products`) — product matching and create/update orchestration.
3. `create_product_draft_resume_file` (route: `/agents/product-drafts/{draft_id}/resume-file`) — draft resume file generation and persistence.
4. `upload_file` (route: `/agents/upload`) — file save + thumbnail generation orchestration.

## Minimal lint rule (manual for now)
- Add a PR checklist item: "No direct infrastructure calls from `api/`".

## Next steps for Phase 1
- Implement `application.use_cases.process_document.execute` (first vertical slice) and wire from `api/agents/files.py`.
- Create ports interfaces for `supabase`, `llm`, `collabora`, `tracing` under `application/ports` or `shared/ports`.

