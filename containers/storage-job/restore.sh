#!/bin/sh
set -e

: "${ARCHIVE_URL:?ARCHIVE_URL is required}"
: "${RESTORE_OP_ID:?RESTORE_OP_ID is required}"
: "${RESTORE_ARCHIVE_KEY:?RESTORE_ARCHIVE_KEY is required}"
: "${RESTORE_DONE_URL:?RESTORE_DONE_URL is required}"

META_URL="${ARCHIVE_URL}.meta"

# Parse done marker URL
DONE_BUCKET=$(echo "$RESTORE_DONE_URL" | sed 's|s3://||' | cut -d/ -f1)
DONE_KEY=$(echo "$RESTORE_DONE_URL" | sed 's|s3://[^/]*/||')

# AWS CLI options
AWS_OPTS=""
[ -n "$AWS_ENDPOINT_URL" ] && AWS_OPTS="--endpoint-url $AWS_ENDPOINT_URL"

# Error handler
trap 'echo "error_code:$?
error_at:$(date -u +%Y-%m-%dT%H:%M:%SZ)
status:failed" | aws $AWS_OPTS s3 cp - "$RESTORE_DONE_URL" || true' ERR

# Idempotency check
echo "Checking for existing done marker..."
if aws $AWS_OPTS s3api head-object --bucket "$DONE_BUCKET" --key "$DONE_KEY" 2>/dev/null; then
	CONTENT=$(aws $AWS_OPTS s3 cp "$RESTORE_DONE_URL" - 2>/dev/null || echo "")
	if [ -z "$CONTENT" ] || echo "$CONTENT" | grep -q "^status:completed"; then
		echo "Already completed ($RESTORE_OP_ID), skipping"
		exit 0
	fi
fi

# Download
echo "Downloading archive..."
aws $AWS_OPTS s3 cp "$ARCHIVE_URL" /tmp/home.tar.zst
echo "Downloading metadata..."
aws $AWS_OPTS s3 cp "$META_URL" /tmp/home.tar.zst.meta

# Verify checksum
echo "Verifying checksum..."
EXPECTED=$(head -n1 /tmp/home.tar.zst.meta | sed 's/^checksum://')
ACTUAL="sha256:$(sha256sum /tmp/home.tar.zst | awk '{print $1}')"
if [ "$EXPECTED" != "$ACTUAL" ]; then
	echo "Checksum mismatch: $EXPECTED != $ACTUAL"
	exit 1
fi

# Extract and sync
echo "Extracting to staging..."
mkdir -p /tmp/staging
zstd -d </tmp/home.tar.zst | tar -xf - -C /tmp/staging
echo "Syncing to /data..."
rsync -a --delete /tmp/staging/ /data/

# Done marker (JSON for Observer to read)
RESTORED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"restore_op_id":"%s","archive_key":"%s","restored_at":"%s"}' "$RESTORE_OP_ID" "$RESTORE_ARCHIVE_KEY" "$RESTORED_AT" |
	aws $AWS_OPTS s3 cp - "$RESTORE_DONE_URL"
echo "Restore complete: $ARCHIVE_URL"
