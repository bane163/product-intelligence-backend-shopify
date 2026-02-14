# Supa Shop AI - Project Overview & Setup Guide

This project consists of a **Shopify App Frontend** (React/Remix/Vite) and a
**Python Backend** (FastAPI/Supabase).

## 📂 Project Structure

- `shopify_supabase_backend/` (Current Directory): The Python backend code,
  running in Docker.
- `extractor-v3/`: The Shopify App frontend and extensions.

---

## 🔄 Overall Flow Walkthrough

The project automates the extraction of product data from spreadsheets and
populates them into Shopify.

1. **User Upload**: The user uploads an Excel (`.xlsx`) or CSV file via the
   Shopify App frontend.
2. **Workflow Initiation**: The FastAPI backend receives the file and triggers
   the LLM workflow via `ctx.services.llm`.
3. **Data Extraction & Visualization**:
   - **Text extraction**: The backend pulls raw data from the cells.
   - **Visual Processing (Excel only)**: For Excel files, the backend uses
     **Collabora Online** to convert the spreadsheet into high-resolution
     images. This gives the AI "visual context" of the spreadsheet layout
     (headers, colors, merged cells).
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
   _This script runs `docker-compose` and optionally starts the LLM._

3. The backend will be available at: `http://localhost:8000`
   - **Swagger UI**: `http://localhost:8000/docs`
   - **Collabora**: `http://localhost:9980` (running as a sidecar service)
   - **LLM**: Starts `ollama run kimi-k2-thinking:cloud` (only if the `--llm`
     flag is passed).

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
