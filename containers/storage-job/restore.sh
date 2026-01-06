#!/bin/sh
set -e

# Environment variables:
# - AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
# - AWS_ENDPOINT_URL (for MinIO/S3-compatible storage)
# - ARCHIVE_URL (s3://bucket/path/home.tar.zst)

# Validate required environment variables
: "${ARCHIVE_URL:?ARCHIVE_URL is required}"

META_URL="${ARCHIVE_URL}.meta"

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

echo "Restore complete: $ARCHIVE_URL"
