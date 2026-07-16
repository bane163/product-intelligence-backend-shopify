#!/usr/bin/env bash
set -e

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

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

echo "🤖 OpenAI is the default LLM provider; custom Ollama endpoints remain configurable in Settings."
