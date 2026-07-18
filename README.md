# Stockpile - Project Overview & Setup Guide

This project consists of a **Shopify App Frontend** (React/Remix/Vite) and a
**Python Backend** (FastAPI/Supabase).

## 📂 Project Structure

- `shopify_supabase_backend/` (Current Directory): The Python backend code,
  running in Docker.
- `extractor-v3/`: The Shopify App frontend and extensions.

### PDF layout extraction

PDF imports use Azure Document Intelligence `prebuilt-layout` first when both
`DOCUMENTINTELLIGENCE_ENDPOINT` and `DOCUMENTINTELLIGENCE_API_KEY` are set.
Digital PDFs safely fall back to PyMuPDF; a scanned or partly image-only PDF is
rejected with `PDF_LAYOUT_UNAVAILABLE` when Azure is unavailable. Use the F0
tier for local trials (noting its multipage limits) and S0 in production for
complete multipage/OCR coverage. Verify access without printing secrets with
`uv run python scripts/check-document-layout.py path/to/file.pdf`.

---

## 🧩 Run the Full Stack Locally (Backend + Supabase + Frontend)

The full Stockpile stack is three processes: the local Supabase stack, the
FastAPI backend (in Docker), and the Shopify App frontend (`shopify app dev`).
`./run.sh` starts the first two; the frontend is started separately.

### Prerequisites (one-time per machine)

- Docker & Docker Compose
- Node.js v20+ and Shopify CLI (`npm install -g @shopify/cli`)
- `npx supabase` (the Supabase CLI; `run.sh` invokes it via `npx`)
- The Shopify CLI must be logged in to a Partner account that owns the
  `extractor_v3` app. On a fresh machine run this once and complete the
  device-code OAuth in your browser:

  ```bash
  shopify auth login
  ```

  The CLI caches the session under `~/Library/Preferences/shopify-cli-*`
  (macOS) so subsequent `shopify app dev` runs are silent.

- The dev store (`teststore163`) must exist in that Partner account and have
  the `extractor_v3` app installed. If the app is not yet installed, the first
  `npm run dev` will surface an install URL to click through.

### Start order (always backend first, frontend second)

```bash
# 1. Backend + local Supabase + offload worker + Collabora sidecar (reuses any
#    already-running collabora container from the same compose project).
cd shopify_supabase_backend
./run.sh
# OpenAI is the default provider; set OPENAI_API_KEY or use Supabase Vault.
```

`run.sh` does, in order:
1. `npx supabase start` — local Supabase stack.
2. `docker-compose -f docker-compose.stack.yml -f docker-compose.debug.yml
   up -d shopify-backend offload-worker` — FastAPI + worker (+ debugpy :5678).

Wait until `curl http://localhost:8000/health` returns `200` before starting
the frontend. The first run downloads/builds Docker images and can take a few
minutes; subsequent runs are fast.

```bash
# 2. Frontend (separate terminal). Must run AFTER the backend is healthy.
cd ../extractor-v3
npm install        # first time only
npm run dev        # == shopify app dev ; provisions the Cloudflare tunnel
```

`shopify app dev` provisions a fresh Cloudflare tunnel each run, so the
tunnel URL (e.g. `https://<words>.trycloudflare.com`) CHANGES on every
restart. The stable entry point is always the Shopify admin app route:

```
https://admin.shopify.com/store/teststore163/apps/extractor_v3
```

Press `P` in the `shopify app dev` terminal to open that URL in your browser.

### Expected ports (host)

| Service            | Port   | Notes                                            |
| ------------------ | ------ | ------------------------------------------------ |
| FastAPI backend    | 8000   | Swagger at `/docs`, debugpy at `5678`            |
| Supabase Kong/API  | 54321  | root returns 404 — that's normal                 |
| Supabase Studio    | 54323  | local DB UI                                       |
| Supabase Postgres  | 54322  | reserved; do not collide with host :5432         |
| Mailpit (SMTP UI)  | 54324  |                                                   |
| Collabora Online   | 9980   | sidecar; reused across `run.sh` invocations       |
| Shopify app dev    | 3000+  | `shopify app dev` picks a free port (e.g. 60688)  |
| Cloudflare tunnel  | random| URL changes every `shopify app dev` restart       |

### Stop everything

```bash
cd shopify_supabase_backend
./stop.sh          # stops backend, offload-worker, supabase, collabora
```

### Notes / gotchas

- The backend image must be built at least once (Python 3.13-slim + uv +
  debugpy). First `run.sh` builds it; subsequent runs reuse the cached image.
- Collabora is part of the same compose project (`shopify_supabase_backend`),
  so if `collabora-online` is already running from a previous `run.sh`, the
  next `run.sh` reuses it rather than starting a duplicate.
- Other Supabase projects on the same machine (e.g. `agent-services-lk`) use
  different port ranges (57xxx) — they do not conflict with this stack, which
  uses 54xxx.
- If `shopify app dev` dies with `Token expired` on a fresh machine, the CLI
  session isn't logged in. Run `shopify auth login`, then restart `npm run dev`.

---

## 🔄 Overall Flow Walkthrough

The project automates the extraction of product data from spreadsheets and other document types
populates them into Shopify.

1. **User Upload**: The user uploads a spreadsheet (`.xlsx`) or CSV file, or other supported document types via the Shopify App frontend.
2. **Workflow Initiation**: The FastAPI backend receives the file and triggers the LLM workflow via `ctx.services.llm`.
3. **Data Extraction & Visualization**:
   - **Text extraction**: The backend pulls raw data from the document (e.g., spreadsheet cells or structured text).
   - **Visual Processing (spreadsheets/documents)**: For supported documents, the backend can use **Collabora Online** to convert the document into high-resolution images. This gives the AI "visual context" of the document layout (headers, colors, merged cells).
4. **AI Agent Analysis**: An autonomous agent receives both the raw text and the
   visual images. It uses a Large Language Model (LLM) to intelligently map the
   messy spreadsheet data into a structured `ProductsList` format.
5. **Structured Response**: The agent's output is validated and parsed into a
   clean JSON structure.
6. **Storefront Sync**: The extracted products are displayed in the frontend,
   where the user can review them before they are created in the Shopify store
   or saved to **Supabase**.

---

## 🚀 Backend Setup (Python/FastAPI)

The backend is containerized using Docker. To start it, you can use the provided
helper script.

### Prerequisites

- Docker & Docker Compose
- Python 3.10+ (optional, for local intellisense)

### How to Run

1. Navigate to this directory:
   ```bash
   cd shopify_supabase_backend
   ```
2. Run the start script:
   ```bash
   ./run.sh
   ```
   _This script runs `docker-compose` (API + offload worker). Configure the
   OpenAI credential through the environment or Supabase Vault before processing._

3. The backend will be available at: `http://localhost:8000`
   - **Swagger UI**: `http://localhost:8000/docs`
   - **Collabora**: `http://localhost:9980` (running as a sidecar service)
   - **LLM**: OpenAI `gpt-5.4-mini` is seeded by default. Custom compatible
     endpoints, including Ollama, can still be configured in Settings.

### Offload Queue Worker

`./run.sh` and `./build.sh` start the durable offload worker (`offload-worker`) alongside the API.
To run it manually outside Docker:

```bash
uv run python offload_worker.py
```

Worker environment variables (set in `.env` as needed; compose defaults are used if omitted):
- `OFFLOAD_QUEUE_NAME` (default: `offload`)
- `OFFLOAD_WORKER_ID` (default: `<hostname>-<pid>`)
- `OFFLOAD_LEASE_SECONDS` (default: `300`)
- `OFFLOAD_POLL_SECONDS` (default: `2.0`)

### Realtime Workflow Updates (Supabase)

For frontend progress updates, use Supabase Realtime subscriptions on:
- `product_drafts` (draft extraction/submit lifecycle)
- `llm_runs` (run status)
- `llm_run_events` (phase-by-phase workflow events)

Recommended client flow:
1. Fetch `/agents/runs/{run_id}/snapshot` (with `x-shop-domain`; optional `draft_id`, `after_seq`, `event_limit`) for initial state.
2. Start realtime subscriptions scoped to `shop_domain` + relevant `run_id`/`draft_id`.
3. On reconnect, call snapshot again with `after_seq` (last seen event sequence) to backfill any missed events.

Mutations still go through backend APIs (`/agents/import`, `/agents/import/batch`, `/agents/submit-products`); realtime is read-only push.

### How to Stop

To shut down all services (Docker containers and Ollama), run:

```bash
./stop.sh
```

### Debugging

The backend runs with `debugpy` enabled on port `5678`. You can attach a VS Code
debugger using the "Python: Attach (Docker debugpy)" configuration.

---

## 🎨 Frontend Setup (Shopify App)

The frontend is a Shopify App built with React Router (formerly Remix).

### Prerequisites

- Node.js (v20+)
- Shopify CLI (`npm install -g @shopify/cli`)

### How to Run

1. Navigate to the frontend directory:
   ```bash
   cd ../extractor-v3
   ```
2. Install dependencies (first time only):
   ```bash
   npm install
   ```
3. Start the development server:
   ```bash
   npm run dev
   ```
   _This command runs `shopify app dev`, which sets up the tunnel and dev
   server._

4. Press `P` in the terminal to open the app in your browser/Shopify admin.

---

## 🔑 Environment Variables

Each directory has its own `.env` file. Ensure they are populated with the
necessary keys (Supabase URL, Service Role Key, Shopify API keys, etc.).

### Vault-backed OpenAI key for install seeding

The backend install seeding flow now resolves OpenAI keys from Supabase Vault first.

Lookup order for a shop domain (for example `seed-shop.myshopify.com`):
1. `openai_api_key__seed-shop.myshopify.com` (shop-specific override)
2. `openai_api_key` (global fallback)
3. `OPENAI_API_KEY` env var (last-resort fallback)

Local SQL examples (using your local DB URL):

```sql
-- Global fallback key
select vault.create_secret('sk-your-global-key', 'openai_api_key', 'Global OpenAI key');

-- Shop-specific override key
select vault.create_secret(
  'sk-your-shop-key',
  'openai_api_key__seed-shop.myshopify.com',
  'Shop-specific OpenAI key'
);
```

Update an existing secret:

```sql
select vault.update_secret('<secret-uuid>', 'sk-new-key', 'openai_api_key', 'Global OpenAI key');
```

---

## Shopify App Store submission workspace

Reviewer-facing submission materials live in:

`specs/001-app-store-readiness/submission/`

Before submitting to Shopify, make sure this workspace includes current values for:

- deployed frontend URL and backend API URL,
- review store domain and reviewer login path,
- sample upload file guidance,
- App Store listing copy and screenshots,
- screencast script,
- support and emergency contact details,
- latest reviewer dry-run evidence.

Use `specs/001-app-store-readiness/quickstart.md` as the release verification
and reviewer rehearsal script.
