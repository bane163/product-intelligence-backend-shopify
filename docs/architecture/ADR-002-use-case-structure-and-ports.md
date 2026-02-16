# ADR-002: Use-case structure and ports

## Status
Proposed

## Context
Phase 1 will extract heavy API route logic into `application` use-cases. To make this reliable and testable, define a standard use-case shape and interface (ports) for external interactions.

## Decision
- Use-case entry points: sync or async functions in `application/use_cases/<feature>.py` named `execute(...)` accepting a small number of primitive args and dependency injection via a `ports` object or keyword args.
- Define ports (interfaces) for external systems: `storage`, `collabora`, `llm`, `shopify`, `tracing`, each exposing minimal methods required by use-cases.
- Implement adapters under `infrastructure/<adapter>.py` that implement these ports and are provided by AppContext in runtime.
- Keep use-cases free of framework types (no FastAPI/HTTP request objects). They return domain DTOs or raise domain-specific exceptions.

## Example use-case signature
```py
# application/use_cases/process_document.py
async def execute(
    file_bytes: bytes,
    filename: str,
    content_type: str,
    ports: Ports,
    *,
    collabora_url: str | None = None,
) -> ProcessResult:
    ...
```

## Consequences
- Pros: testable use-cases, clear interface contracts, easier to mock adapters in tests.
- Cons: initial overhead defining ports and refactoring.

