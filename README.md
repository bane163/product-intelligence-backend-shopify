# Supa Shop AI - Project Overview & Setup Guide

This project consists of a **Shopify App Frontend** (React/Remix/Vite) and a
**Python Backend** (FastAPI/Supabase).

## 📂 Project Structure

- `shopify_supabase_backend/` (Current Directory): The Python backend code,
  running in Docker.
- `extractor-v3/`: The Shopify App frontend and extensions.

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
   _Note: Add `--llm` if you want to also start the Ollama model:_
   ```bash
   ./run.sh --llm
   ```
   _This script runs `docker-compose` (API + offload worker) and optionally starts the LLM._

3. The backend will be available at: `http://localhost:8000`
   - **Swagger UI**: `http://localhost:8000/docs`
   - **Collabora**: `http://localhost:9980` (running as a sidecar service)
   - **LLM**: Starts `ollama run kimi-k2-thinking:cloud` (only if the `--llm`
     flag is passed).

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
