#!/usr/bin/env bash
set -euo pipefail

# Navigate to repository directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PREV_COMMIT=$(git rev-parse HEAD)

rollback() {
    echo ""
    echo "================================================="
    echo "⚠️  Deployment failed! Initiating automatic rollback..."
    echo "================================================="
    echo "Rolling back git to previous commit: $PREV_COMMIT"
    git reset --hard "$PREV_COMMIT"

    echo "Rebuilding and restoring previous working container..."
    docker compose down || true
    docker compose up -d --build

    sleep 4
    ROLLBACK_RESPONSE=$(curl -s "http://localhost:9000/health" || true)
    if [[ "$ROLLBACK_RESPONSE" == *"ok"* ]]; then
        echo "✅ Rollback complete. Restored to stable commit $PREV_COMMIT."
    else
        echo "❌ Emergency: Rollback health check failed. Inspect logs: docker compose logs app"
    fi
    exit 1
}

# Trap any command errors and trigger rollback
trap 'rollback' ERR

echo "========================================="
echo "🔄 Starting redeployment: croi-8"
echo "Current commit: $PREV_COMMIT"
echo "========================================="

# 1. Stop existing containers
echo "🛑 Stopping existing containers..."
docker compose down || true

# 2. Pull latest git changes
echo "📥 Pulling latest changes from git..."
git pull origin main

NEW_COMMIT=$(git rev-parse HEAD)
echo "Updated to commit: $NEW_COMMIT"

# 3. Rebuild and launch containers in background
echo "🏗️  Rebuilding and launching containers with docker compose..."
docker compose up -d --build

# 4. Wait for service readiness
echo "⏳ Waiting for service readiness..."
sleep 4

HEALTH_URL="http://localhost:9000/health"
MAX_RETRIES=10
RETRY_COUNT=0
HEALTHY=false

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    RESPONSE=$(curl -s "$HEALTH_URL" || true)
    if [[ "$RESPONSE" == *"ok"* ]]; then
        HEALTHY=true
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    echo "Waiting for health check ($RETRY_COUNT/$MAX_RETRIES)..."
    sleep 2
done

if [ "$HEALTHY" = true ]; then
    # Disable error trap since deployment succeeded
    trap - ERR
    echo "========================================="
    echo "✅ Redeployment successful!"
    echo "Commit: $NEW_COMMIT"
    echo "Health response: $RESPONSE"
    echo "========================================="
    exit 0
else
    echo "Health check failed after $MAX_RETRIES attempts."
    rollback
fi
