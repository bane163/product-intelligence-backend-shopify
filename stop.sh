#!/usr/bin/env bash
set -e

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "🛑 Stopping Shopify Backend (Docker)..."

# Stop the backend and its dependencies
docker-compose -f docker-compose.stack.yml -f docker-compose.debug.yml down

# Stop Supabase (if installed via npm)
if command -v npx &> /dev/null; then
  echo "🔁 Stopping Supabase (npx supabase stop)..."
  npx supabase stop || echo "⚠️ Supabase stop failed or already stopped"
else
  echo "⚠️ npx not available; skipping Supabase stop"
fi

echo "✅ All services stopped."
