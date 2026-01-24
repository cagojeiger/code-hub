#!/bin/sh
set -e

# Environment variables:
# - AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
# - AWS_ENDPOINT_URL (for MinIO/S3-compatible storage)
# - ARCHIVE_URL (s3://bucket/path/home.tar.zst)
# - ARCHIVE_OP_ID (optional, for error marker)

# Validate required environment variables
: "${ARCHIVE_URL:?ARCHIVE_URL is required}"

# Parse bucket and key from s3://bucket/key format
BUCKET=$(echo "$ARCHIVE_URL" | sed 's|s3://||' | cut -d/ -f1)
KEY=$(echo "$ARCHIVE_URL" | sed 's|s3://[^/]*/||')
META_URL="${ARCHIVE_URL}.meta"

# Error marker URL: same directory as archive
ERROR_URL=$(dirname "$ARCHIVE_URL")/.error

# AWS CLI options for S3-compatible storage
AWS_OPTS=""
if [ -n "$AWS_ENDPOINT_URL" ]; then
    AWS_OPTS="--endpoint-url $AWS_ENDPOINT_URL"
fi

# Error handler: upload error marker to S3 on failure
upload_error_marker() {
    ERROR_CODE=$?
    ERROR_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    echo "Uploading error marker (exit code: $ERROR_CODE)..."
    cat > /tmp/error_marker.json <<EOF
{
  "status": "Failed",
  "operation": "archive",
  "archive_op_id": "${ARCHIVE_OP_ID:-unknown}",
  "error_code": $ERROR_CODE,
  "error_at": "$ERROR_TIME"
}
EOF
    aws $AWS_OPTS s3 cp /tmp/error_marker.json "$ERROR_URL" || true
}
trap upload_error_marker ERR

# 1. HEAD check - idempotent (skip if both tar.zst and .meta exist)
if aws $AWS_OPTS s3api head-object --bucket "$BUCKET" --key "$KEY" 2>/dev/null && \
   aws $AWS_OPTS s3api head-object --bucket "$BUCKET" --key "${KEY}.meta" 2>/dev/null; then
    echo "Already complete, skipping"
    exit 0
fi

# 2. tar + zstd compression (exclude sockets)
echo "Compressing /data..."
tar --exclude='*.sock' --exclude='*.socket' -cf - -C /data . | zstd -o /tmp/home.tar.zst

# 3. sha256 checksum
echo "Computing checksum..."
sha256sum /tmp/home.tar.zst | awk '{print "sha256:"$1}' > /tmp/home.tar.zst.meta

# 4. S3 upload (order matters: tar.zst first, .meta last as commit marker)
echo "Uploading archive..."
aws $AWS_OPTS s3 cp /tmp/home.tar.zst "$ARCHIVE_URL"

echo "Uploading metadata..."
aws $AWS_OPTS s3 cp /tmp/home.tar.zst.meta "$META_URL"

# 6. Delete error marker if exists (cleanup on success)
# Note: .meta 존재 = 성공 판정이므로 삭제 실패해도 무방 (precedence rule)
echo "Cleaning up error marker..."
aws $AWS_OPTS s3 rm "$ERROR_URL" 2>/dev/null || true

echo "Archive complete: $ARCHIVE_URL"
