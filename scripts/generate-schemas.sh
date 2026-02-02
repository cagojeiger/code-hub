#!/bin/bash
# Generate Pydantic models from OpenAPI spec
# Usage: ./scripts/generate-schemas.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
SRC_DIR="$ROOT_DIR/src"

OPENAPI_SPEC="$ROOT_DIR/api/openapi.yaml"
AGENT_OUTPUT="$ROOT_DIR/src/codehub_agent/api/v1/schemas.py"
WC_OUTPUT="$ROOT_DIR/src/codehub/core/schemas/agent_api.py"

echo "Generating schemas from $OPENAPI_SPEC..."

# Ensure output directories exist
mkdir -p "$(dirname "$AGENT_OUTPUT")"
mkdir -p "$(dirname "$WC_OUTPUT")"

# Common options for datamodel-codegen
COMMON_OPTS=(
  --input "$OPENAPI_SPEC"
  --output-model-type pydantic_v2.BaseModel
  --use-union-operator
  --target-python-version 3.13
  --collapse-root-models
  --field-constraints
  --use-standard-collections
  --disable-timestamp
)

# Generate Agent schemas
echo "  -> $AGENT_OUTPUT"
cd "$SRC_DIR" && uv run datamodel-codegen \
  "${COMMON_OPTS[@]}" \
  --output "$AGENT_OUTPUT"

# Generate WC schemas
echo "  -> $WC_OUTPUT"
cd "$SRC_DIR" && uv run datamodel-codegen \
  "${COMMON_OPTS[@]}" \
  --output "$WC_OUTPUT"

echo "Done!"
