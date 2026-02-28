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

echo "🛑 Stopping running services before rebuild..."
if [ -x "./stop.sh" ]; then
  ./stop.sh
else
  docker-compose -f docker-compose.stack.yml -f docker-compose.debug.yml down
fi

echo "🔨 Rebuilding Shopify backend container..."
echo "♻️ Refreshing backend anonymous volumes so dependency changes are applied..."
docker-compose -f docker-compose.stack.yml -f docker-compose.debug.yml up -d --build --force-recreate --renew-anon-volumes shopify-backend offload-worker
echo "✅ Backend and worker rebuilt and started."
echo "💡 Dependency updates (like new Python packages) require running this build script."

if [ "$RUN_LLM" = true ]; then
  if command -v ollama &> /dev/null; then
    echo "🦙 Starting Ollama model (kimi-k2-thinking:cloud)..."
    ollama run kimi-k2-thinking:cloud
  else
    echo "⚠️ Ollama not found; skipping LLM start."
  fi
else
  echo "💡 Tip: Run './build.sh --llm' to start the Ollama model too."
fi
