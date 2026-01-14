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

# AWS CLI options for S3-compatible storage
AWS_OPTS=""
if [ -n "$AWS_ENDPOINT_URL" ]; then
    AWS_OPTS="--endpoint-url $AWS_ENDPOINT_URL"
fi

# 1. Download archive and metadata
echo "Downloading archive..."
aws $AWS_OPTS s3 cp "$ARCHIVE_URL" /tmp/home.tar.zst

echo "Downloading metadata..."
aws $AWS_OPTS s3 cp "$META_URL" /tmp/home.tar.zst.meta

# 2. Verify checksum
echo "Verifying checksum..."
EXPECTED=$(cat /tmp/home.tar.zst.meta)
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

echo "Restore complete: $ARCHIVE_URL"
