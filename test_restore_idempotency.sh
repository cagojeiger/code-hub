#!/bin/bash
set -e

echo "=========================================="
echo "Restore Idempotency Integration Test"
echo "=========================================="

# Configuration
MINIO_ENDPOINT="http://localhost:19000"
MINIO_HOST="minio:9000"
BUCKET="codehub-archives"
PREFIX="test-idempotency"
WS_ID="test-ws-001"
ARCHIVE_OP_ID="archive-001"
RESTORE_OP_ID_1="restore-001"
RESTORE_OP_ID_2="restore-002"
AWS_ACCESS_KEY="codehub"
AWS_SECRET_KEY="codehub123"

# Paths
ARCHIVE_KEY="${PREFIX}/${WS_ID}/${ARCHIVE_OP_ID}/home.tar.zst"
ARCHIVE_URL="s3://${BUCKET}/${ARCHIVE_KEY}"
MARKER_KEY="${PREFIX}/${WS_ID}/.restore_marker"
MARKER_URL="s3://${BUCKET}/${MARKER_KEY}"

# Docker network
NETWORK="codehub-net"

# Cleanup function
cleanup() {
    echo ""
    echo "Cleaning up test resources..."
    docker run --rm --network ${NETWORK} \
        -e AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY} \
        -e AWS_SECRET_ACCESS_KEY=${AWS_SECRET_KEY} \
        amazon/aws-cli \
        --endpoint-url http://${MINIO_HOST} \
        s3 rm s3://${BUCKET}/${PREFIX}/ --recursive || true

    docker volume rm test-restore-vol || true
    echo "Cleanup complete"
}

# Trap cleanup on exit
trap cleanup EXIT

echo ""
echo "Step 1: Create test archive in MinIO"
echo "--------------------------------------"

# Create test data
mkdir -p /tmp/test-restore-data
echo "Test file content $(date)" > /tmp/test-restore-data/test.txt

# Create archive with zstd compression (matching archive.sh format)
tar -cf - -C /tmp/test-restore-data . | docker run --rm -i alpine:3.19 sh -c "apk add --no-cache zstd > /dev/null 2>&1 && zstd" > /tmp/test-archive.tar.zst

# Upload to MinIO
docker run --rm --network ${NETWORK} \
    -v /tmp/test-archive.tar.zst:/tmp/archive.tar.zst:ro \
    -e AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY} \
    -e AWS_SECRET_ACCESS_KEY=${AWS_SECRET_KEY} \
    amazon/aws-cli \
    --endpoint-url http://${MINIO_HOST} \
    s3 cp /tmp/archive.tar.zst ${ARCHIVE_URL}

# Create .meta file
echo "sha256:$(shasum -a 256 /tmp/test-archive.tar.zst | awk '{print $1}')" > /tmp/archive.meta
docker run --rm --network ${NETWORK} \
    -v /tmp/archive.meta:/tmp/meta:ro \
    -e AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY} \
    -e AWS_SECRET_ACCESS_KEY=${AWS_SECRET_KEY} \
    amazon/aws-cli \
    --endpoint-url http://${MINIO_HOST} \
    s3 cp /tmp/meta ${ARCHIVE_URL}.meta

echo "✓ Test archive created at ${ARCHIVE_URL}"

echo ""
echo "Step 2: First restore with restore-001"
echo "--------------------------------------"

# Create test volume
docker volume create test-restore-vol

# Run restore.sh with restore_op_id=restore-001
docker run --rm --network ${NETWORK} \
    -v test-restore-vol:/data \
    -e AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY} \
    -e AWS_SECRET_ACCESS_KEY=${AWS_SECRET_KEY} \
    -e AWS_ENDPOINT_URL=http://${MINIO_HOST} \
    -e ARCHIVE_URL=${ARCHIVE_URL} \
    -e RESTORE_OP_ID=${RESTORE_OP_ID_1} \
    -e RESTORE_ARCHIVE_KEY=${ARCHIVE_KEY} \
    -e WORKSPACE_ID=${WS_ID} \
    -e S3_PREFIX=${PREFIX}/ \
    -e S3_BUCKET=${BUCKET} \
    codehub/storage-job:latest \
    /usr/local/bin/restore

echo "✓ First restore completed"

# Verify .restore_marker exists and has correct restore_op_id
echo ""
echo "Step 3: Verify .restore_marker"
echo "--------------------------------------"

MARKER_CONTENT=$(docker run --rm --network ${NETWORK} \
    -e AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY} \
    -e AWS_SECRET_ACCESS_KEY=${AWS_SECRET_KEY} \
    amazon/aws-cli \
    --endpoint-url http://${MINIO_HOST} \
    s3 cp ${MARKER_URL} -)

echo "Marker content: ${MARKER_CONTENT}"

# Check if restore_op_id matches
if echo "${MARKER_CONTENT}" | jq -e ".restore_op_id == \"${RESTORE_OP_ID_1}\"" > /dev/null; then
    echo "✓ .restore_marker has correct restore_op_id: ${RESTORE_OP_ID_1}"
else
    echo "✗ ERROR: .restore_marker has incorrect restore_op_id"
    exit 1
fi

echo ""
echo "Step 4: Second restore with same restore-001 (should skip)"
echo "-----------------------------------------------------------"

# Run restore.sh again with same restore_op_id
OUTPUT=$(docker run --rm --network ${NETWORK} \
    -v test-restore-vol:/data \
    -e AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY} \
    -e AWS_SECRET_ACCESS_KEY=${AWS_SECRET_KEY} \
    -e AWS_ENDPOINT_URL=http://${MINIO_HOST} \
    -e ARCHIVE_URL=${ARCHIVE_URL} \
    -e RESTORE_OP_ID=${RESTORE_OP_ID_1} \
    -e RESTORE_ARCHIVE_KEY=${ARCHIVE_KEY} \
    -e WORKSPACE_ID=${WS_ID} \
    -e S3_PREFIX=${PREFIX}/ \
    -e S3_BUCKET=${BUCKET} \
    codehub/storage-job:latest \
    /usr/local/bin/restore 2>&1)

echo "Output: ${OUTPUT}"

# Check if it skipped
if echo "${OUTPUT}" | grep -q "Already restored with same restore_op_id"; then
    echo "✓ Idempotency check works! Skipped re-download"
else
    echo "✗ ERROR: Did not skip - idempotency check failed"
    exit 1
fi

echo ""
echo "Step 5: Third restore with different restore-002 (should proceed)"
echo "------------------------------------------------------------------"

# Run restore.sh with different restore_op_id
OUTPUT=$(docker run --rm --network ${NETWORK} \
    -v test-restore-vol:/data \
    -e AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY} \
    -e AWS_SECRET_ACCESS_KEY=${AWS_SECRET_KEY} \
    -e AWS_ENDPOINT_URL=http://${MINIO_HOST} \
    -e ARCHIVE_URL=${ARCHIVE_URL} \
    -e RESTORE_OP_ID=${RESTORE_OP_ID_2} \
    -e RESTORE_ARCHIVE_KEY=${ARCHIVE_KEY} \
    -e WORKSPACE_ID=${WS_ID} \
    -e S3_PREFIX=${PREFIX}/ \
    -e S3_BUCKET=${BUCKET} \
    codehub/storage-job:latest \
    /usr/local/bin/restore 2>&1)

echo "Output: ${OUTPUT}"

# Check if it proceeded
if echo "${OUTPUT}" | grep -q "Different restore_op_id detected"; then
    echo "✓ Different restore_op_id detected - proceeding with new restore"
else
    echo "✗ ERROR: Did not detect different restore_op_id"
    exit 1
fi

# Verify marker was updated
MARKER_CONTENT=$(docker run --rm --network ${NETWORK} \
    -e AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY} \
    -e AWS_SECRET_ACCESS_KEY=${AWS_SECRET_KEY} \
    amazon/aws-cli \
    --endpoint-url http://${MINIO_HOST} \
    s3 cp ${MARKER_URL} -)

if echo "${MARKER_CONTENT}" | jq -e ".restore_op_id == \"${RESTORE_OP_ID_2}\"" > /dev/null; then
    echo "✓ .restore_marker updated with new restore_op_id: ${RESTORE_OP_ID_2}"
else
    echo "✗ ERROR: .restore_marker was not updated"
    exit 1
fi

echo ""
echo "=========================================="
echo "✓ ALL TESTS PASSED"
echo "=========================================="
echo ""
echo "Summary:"
echo "  1. ✓ Archive created successfully"
echo "  2. ✓ First restore completed"
echo "  3. ✓ .restore_marker created with correct restore_op_id"
echo "  4. ✓ Idempotency check: Same restore_op_id → skip"
echo "  5. ✓ Different restore_op_id → proceed and update marker"
