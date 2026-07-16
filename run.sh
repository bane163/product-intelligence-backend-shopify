#!/usr/bin/env bash
set -e

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "🚀 Starting Shopify Backend (Docker)..."

# Start Supabase (if installed via npm)
if command -v npx &> /dev/null; then
  echo "🔁 Starting Supabase (npx supabase start --ignore-health-check)..."
  # Local Supabase can report transient unhealthy services during startup while
  # the core database/API stack still comes online from the existing backup.
  npx supabase start --ignore-health-check || echo "⚠️ Supabase start failed or already running"
else
  echo "⚠️ npx not available; skipping Supabase start"
fi

# Run the backend, worker, and dependencies
docker compose -f docker-compose.stack.yml up -d --build shopify-backend offload-worker

echo "⏳ Waiting for backend and Collabora viewer readiness..."
ready=0
for _ in $(seq 1 180); do
  if curl -fsS http://127.0.0.1:8000/ready >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "❌ Backend started but did not become ready within 180 seconds"
  exit 1
fi

echo "✅ Backend and worker started!"
echo "📝 Use 'docker compose -f docker-compose.stack.yml logs -f shopify-backend offload-worker' to see logs."

echo "🤖 OpenAI is the default LLM provider; configure OPENAI_API_KEY or Supabase Vault before processing."
