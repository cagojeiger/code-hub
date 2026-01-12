#!/bin/sh
set -e

# Environment variables:
# - AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
# - AWS_ENDPOINT_URL (for MinIO/S3-compatible storage)
# - ARCHIVE_URL (s3://bucket/path/home.tar.zst)

# Validate required environment variables
: "${ARCHIVE_URL:?ARCHIVE_URL is required}"

# Parse bucket and key from s3://bucket/key format
BUCKET=$(echo "$ARCHIVE_URL" | sed 's|s3://||' | cut -d/ -f1)
KEY=$(echo "$ARCHIVE_URL" | sed 's|s3://[^/]*/||')
META_URL="${ARCHIVE_URL}.meta"

# AWS CLI options for S3-compatible storage
AWS_OPTS=""
if [ -n "$AWS_ENDPOINT_URL" ]; then
    AWS_OPTS="--endpoint-url $AWS_ENDPOINT_URL"
fi

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

echo "Archive complete: $ARCHIVE_URL"
