#!/bin/sh
set -e

: "${ARCHIVE_URL:?ARCHIVE_URL is required}"

# Parse S3 URL
BUCKET=$(echo "$ARCHIVE_URL" | sed 's|s3://||' | cut -d/ -f1)
KEY=$(echo "$ARCHIVE_URL" | sed 's|s3://[^/]*/||')
META_URL="${ARCHIVE_URL}.meta"

# AWS CLI options
AWS_OPTS=""
[ -n "$AWS_ENDPOINT_URL" ] && AWS_OPTS="--endpoint-url $AWS_ENDPOINT_URL"

# Error handler
trap 'echo "error_code:$?
error_at:$(date -u +%Y-%m-%dT%H:%M:%SZ)
status:failed" | aws $AWS_OPTS s3 cp - "$META_URL" || true' ERR

# Idempotency check
echo "Checking for existing archive..."
if aws $AWS_OPTS s3api head-object --bucket "$BUCKET" --key "${KEY}.meta" 2>/dev/null; then
    CONTENT=$(aws $AWS_OPTS s3 cp "$META_URL" - 2>/dev/null || echo "")
    STATUS=$(echo "$CONTENT" | grep "^status:" | cut -d: -f2-)
    [ -z "$STATUS" ] && case "$(echo "$CONTENT" | head -n1)" in sha256:*|checksum:*) STATUS="completed";; esac
    if [ "$STATUS" = "completed" ] && aws $AWS_OPTS s3api head-object --bucket "$BUCKET" --key "$KEY" 2>/dev/null; then
        echo "Already complete, skipping"
        exit 0
    fi
fi

# Compress
echo "Compressing /data..."
tar --exclude='*.sock' --exclude='*.socket' -cf - -C /data . | zstd -o /tmp/home.tar.zst

# Create metadata
CHECKSUM="sha256:$(sha256sum /tmp/home.tar.zst | awk '{print $1}')"
echo "checksum:$CHECKSUM
archived_at:$(date -u +%Y-%m-%dT%H:%M:%SZ)
status:completed" > /tmp/home.tar.zst.meta

# Upload
echo "Uploading archive..."
aws $AWS_OPTS s3 cp /tmp/home.tar.zst "$ARCHIVE_URL"
echo "Uploading metadata..."
aws $AWS_OPTS s3 cp /tmp/home.tar.zst.meta "$META_URL"

echo "Archive complete: $ARCHIVE_URL"
