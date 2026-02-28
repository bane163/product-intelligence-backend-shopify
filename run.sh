#!/usr/bin/env bash
set -e

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Parse arguments
RUN_LLM=false
for arg in "$@"; do
  if [ "$arg" == "--llm" ]; then
    RUN_LLM=true
  fi
done

echo "🚀 Starting Shopify Backend in Debug Mode (Docker)..."

# Start Supabase (if installed via npm)
if command -v npx &> /dev/null; then
  echo "🔁 Starting Supabase (npx supabase start)..."
  npx supabase start || echo "⚠️ Supabase start failed or already running"
else
  echo "⚠️ npx not available; skipping Supabase start"
fi

# Run the backend, worker, and dependencies
docker-compose -f docker-compose.stack.yml -f docker-compose.debug.yml up -d shopify-backend offload-worker

echo "✅ Backend and worker started! You can attach the VS Code debugger now."
echo "📝 Use 'docker-compose -f docker-compose.stack.yml -f docker-compose.debug.yml logs -f shopify-backend offload-worker' to see logs."

# Start the LLM model if requested
if [ "$RUN_LLM" = true ]; then
  echo "🦙 Starting Ollama model (kimi-k2-thinking:cloud)..."
  ollama run kimi-k2-thinking:cloud
else
  echo "💡 Tip: Run with './run.sh --llm' if you want to start the Ollama model as well."
fi
