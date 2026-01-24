#!/bin/sh
set -e

# Environment variables:
# - AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
# - AWS_ENDPOINT_URL (for MinIO/S3-compatible storage)
# - ARCHIVE_URL (s3://bucket/path/home.tar.zst)
# - RESTORE_OP_ID (restore operation ID for marker)
# - RESTORE_ARCHIVE_KEY (archive key for marker)
# - WORKSPACE_ID (workspace ID for marker path)
# - S3_PREFIX (S3 prefix for marker path)
# - S3_BUCKET (S3 bucket for marker path)

# Validate required environment variables
: "${ARCHIVE_URL:?ARCHIVE_URL is required}"
: "${RESTORE_OP_ID:?RESTORE_OP_ID is required}"
: "${RESTORE_ARCHIVE_KEY:?RESTORE_ARCHIVE_KEY is required}"
: "${WORKSPACE_ID:?WORKSPACE_ID is required}"
: "${S3_PREFIX:?S3_PREFIX is required}"
: "${S3_BUCKET:?S3_BUCKET is required}"

META_URL="${ARCHIVE_URL}.meta"
MARKER_KEY="${S3_PREFIX}${WORKSPACE_ID}/.restore_marker"
MARKER_URL="s3://${S3_BUCKET}/${MARKER_KEY}"
ERROR_KEY="${S3_PREFIX}${WORKSPACE_ID}/.restore_error"
ERROR_URL="s3://${S3_BUCKET}/${ERROR_KEY}"

# AWS CLI options for S3-compatible storage
AWS_OPTS=""
if [ -n "$AWS_ENDPOINT_URL" ]; then
    AWS_OPTS="--endpoint-url $AWS_ENDPOINT_URL"
fi

# Error handler: upload error marker to S3 on failure
upload_error_marker() {
    ERROR_CODE=$?
    ERROR_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    echo "Uploading restore error marker (exit code: $ERROR_CODE)..."
    cat > /tmp/restore_error.json <<EOF
{
  "status": "Failed",
  "operation": "restore",
  "restore_op_id": "${RESTORE_OP_ID}",
  "archive_key": "${RESTORE_ARCHIVE_KEY}",
  "error_code": $ERROR_CODE,
  "error_at": "$ERROR_TIME"
}
EOF
    aws $AWS_OPTS s3 cp /tmp/restore_error.json "$ERROR_URL" || true
}
trap upload_error_marker ERR

# 0. Idempotency check: skip if already completed with same restore_op_id
echo "Checking for existing restore marker..."
if aws $AWS_OPTS s3api head-object --bucket "$S3_BUCKET" --key "$MARKER_KEY" 2>/dev/null; then
    echo "Restore marker found, checking restore_op_id..."
    EXISTING_CONTENT=$(aws $AWS_OPTS s3 cp "$MARKER_URL" - 2>/dev/null || echo "{}")
    EXISTING_OP_ID=$(echo "$EXISTING_CONTENT" | jq -r '.restore_op_id // empty' 2>/dev/null || echo "")

    if [ -n "$EXISTING_OP_ID" ] && [ "$EXISTING_OP_ID" = "$RESTORE_OP_ID" ]; then
        echo "Already restored with same restore_op_id ($RESTORE_OP_ID), skipping"
        exit 0
    fi

    echo "Different restore_op_id detected (existing: $EXISTING_OP_ID, requested: $RESTORE_OP_ID)"
    echo "Proceeding with new restore..."
fi

# 1. Download archive and metadata
echo "Downloading archive..."
aws $AWS_OPTS s3 cp "$ARCHIVE_URL" /tmp/home.tar.zst

echo "Downloading metadata..."
aws $AWS_OPTS s3 cp "$META_URL" /tmp/home.tar.zst.meta

# 2. Verify checksum
echo "Verifying checksum..."
EXPECTED=$(head -n 1 /tmp/home.tar.zst.meta)
ACTUAL="sha256:$(sha256sum /tmp/home.tar.zst | awk '{print $1}')"

if [ "$EXPECTED" != "$ACTUAL" ]; then
    echo "Checksum mismatch: $EXPECTED != $ACTUAL"
    exit 1
fi
echo "Checksum verified"

# 3. Extract to staging directory
echo "Extracting to staging..."
mkdir -p /tmp/staging
zstd -d < /tmp/home.tar.zst | tar -xf - -C /tmp/staging

# 4. rsync --delete to /data (atomic sync)
echo "Syncing to /data..."
rsync -a --delete /tmp/staging/ /data/

# 5. Upload .restore_marker to S3 (completion marker)
echo "Uploading restore marker..."
RESTORED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat > /tmp/restore_marker.json <<EOF
{
  "restore_op_id": "${RESTORE_OP_ID}",
  "archive_key": "${RESTORE_ARCHIVE_KEY}",
  "restored_at": "${RESTORED_AT}"
}
EOF
aws $AWS_OPTS s3 cp /tmp/restore_marker.json "$MARKER_URL"

# 6. Upload .volume_origin to S3 (volume source tracking)
echo "Uploading volume origin marker..."
ORIGIN_KEY="${S3_PREFIX}${WORKSPACE_ID}/.volume_origin"
ORIGIN_URL="s3://${S3_BUCKET}/${ORIGIN_KEY}"
cat > /tmp/volume_origin.json <<EOF
{
  "kind": "restored",
  "source_archive_key": "${RESTORE_ARCHIVE_KEY}",
  "restore_op_id": "${RESTORE_OP_ID}",
  "restored_at": "${RESTORED_AT}"
}
EOF
aws $AWS_OPTS s3 cp /tmp/volume_origin.json "$ORIGIN_URL"

# 7. Delete error marker if exists (cleanup on success)
# Note: .restore_marker 존재 = 성공 판정이므로 삭제 실패해도 무방 (precedence rule)
echo "Cleaning up restore error marker..."
aws $AWS_OPTS s3 rm "$ERROR_URL" 2>/dev/null || true

echo "Restore complete: $ARCHIVE_URL"
