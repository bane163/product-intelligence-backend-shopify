# shopify_supabase_backend Development Guidelines

Auto-generated from feature plans. Last updated: 2026-04-11

## Active Technologies

- Python 3.13 backend; TypeScript 5.9 / React Router 7 frontend + FastAPI, Supabase, Shopify App React Router, App Bridge, encrypted Supabase session storage, Vitest, pytest (001-app-store-readiness)

## Project Structure

```text
shopify_supabase_backend/
extractor-v3/
```

## Commands

- Backend: `cd /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend && PYTHONPATH=. uv run pytest -q tests/test_release_readiness_gates.py`
- Frontend: `cd /Users/freemansenecharles/developer/server/shopify_extractor/extractor-v3 && npm run lint && npm run typecheck && npm run test:contract && npm run build`

## Code Style

Follow existing FastAPI, React Router, Shopify embedded app, and Supabase patterns already present in the two repositories.

## Recent Changes

- 001-app-store-readiness: Added dual-repo Shopify App Store readiness planning context for FastAPI/Supabase backend and React Router/App Bridge frontend

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
