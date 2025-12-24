#!/usr/bin/env bash
set -e

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "🚀 Starting Shopify Backend in Debug Mode (Docker)..."

# Run the backend and its dependencies
docker-compose -f docker-compose.stack.yml -f docker-compose.debug.yml up -d shopify-backend

echo "✅ Backend started! You can attach the VS Code debugger now."
echo "📝 Use 'docker-compose -f docker-compose.stack.yml -f docker-compose.debug.yml logs -f shopify-backend' to see logs."
