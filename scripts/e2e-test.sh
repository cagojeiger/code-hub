#!/bin/bash
# E2E Test Runner
#
# Usage:
#   ./scripts/e2e-test.sh           # Run all E2E tests
#   ./scripts/e2e-test.sh -k owner  # Run specific tests
#   ./scripts/e2e-test.sh --keep    # Keep containers running after tests

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Parse arguments
KEEP_RUNNING=false
PYTEST_ARGS=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --keep)
            KEEP_RUNNING=true
            shift
            ;;
        *)
            PYTEST_ARGS="$PYTEST_ARGS $1"
            shift
            ;;
    esac
done

cd "$PROJECT_ROOT"

# Clean up any previous E2E containers and data
echo "=== Cleaning up previous E2E environment ==="
docker compose -f docker-compose.e2e.yml down -v 2>/dev/null || true

# Remove E2E workspace containers (not infra)
for container in $(docker ps -a --filter "name=codehub-e2e-" --format "{{.Names}}" 2>/dev/null); do
    if [[ ! "$container" =~ ^codehub-e2e-(backend|postgres|redis|docker-proxy|migrate)$ ]]; then
        echo "Removing E2E workspace container: $container"
        docker rm -f "$container" 2>/dev/null || true
    fi
done

# Clean up E2E data directory
rm -rf "$PROJECT_ROOT/data/e2e"

# Create E2E data directory with proper permissions (uid 1000)
echo "=== Creating E2E data directory ==="
mkdir -p "$PROJECT_ROOT/data/e2e/homes"
# Set ownership to match container user (1000:1000)
# Use sudo if available and needed, otherwise try without
if [ "$(id -u)" != "1000" ]; then
    if command -v sudo &> /dev/null; then
        sudo chown -R 1000:1000 "$PROJECT_ROOT/data/e2e" 2>/dev/null || \
            chmod -R 777 "$PROJECT_ROOT/data/e2e"
    else
        chmod -R 777 "$PROJECT_ROOT/data/e2e"
    fi
fi

echo "=== Starting E2E test environment ==="
docker compose -f docker-compose.e2e.yml up -d --build --wait

echo ""
echo "=== Running E2E tests ==="
cd backend
E2E_BASE_URL=http://localhost:8080 uv run pytest tests/e2e -v $PYTEST_ARGS
TEST_EXIT_CODE=$?
cd "$PROJECT_ROOT"

if [ "$KEEP_RUNNING" = false ]; then
    echo ""
    echo "=== Cleaning up ==="
    docker compose -f docker-compose.e2e.yml down -v

    # Remove E2E workspace containers
    for container in $(docker ps -a --filter "name=codehub-e2e-" --format "{{.Names}}" 2>/dev/null); do
        if [[ ! "$container" =~ ^codehub-e2e-(backend|postgres|redis|docker-proxy|migrate)$ ]]; then
            docker rm -f "$container" 2>/dev/null || true
        fi
    done

    # Clean up E2E data
    rm -rf "$PROJECT_ROOT/data/e2e"
fi

exit $TEST_EXIT_CODE
