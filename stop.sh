#!/usr/bin/env bash
set -e

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "🛑 Stopping Shopify Backend (Docker)..."

# Stop the backend and its dependencies
docker-compose -f docker-compose.stack.yml -f docker-compose.debug.yml down

# Optionally stop Ollama if it's running the specific model
if command -v ollama &> /dev/null; then
    echo "🦙 Stopping Ollama model (kimi-k2-thinking:cloud) if running..."
    ollama stop kimi-k2-thinking:cloud || true
fi

echo "✅ All services stopped."
