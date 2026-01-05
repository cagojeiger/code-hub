"""Tests for ActivityBuffer and ActivityStore.

Reference: docs/architecture_v2/ttl-manager.md
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
import redis.asyncio as redis

from codehub.app.proxy.activity import ActivityBuffer, get_activity_buffer
from codehub.infra.redis_kv import ActivityStore


class TestActivityBuffer:
    """ActivityBuffer unit tests."""

    def test_record_stores_timestamp(self):
        """record() stores current timestamp in buffer."""
        buffer = ActivityBuffer()

        before = time.time()
        buffer.record("ws-1")
        after = time.time()

        assert "ws-1" in buffer._buffer
        assert before <= buffer._buffer["ws-1"] <= after

    def test_record_updates_existing(self):
        """record() updates timestamp for existing workspace (when not throttled)."""
        buffer = ActivityBuffer(throttle_sec=0)  # Disable throttling for this test

        buffer.record("ws-1")
        old_ts = buffer._buffer["ws-1"]

        time.sleep(0.01)  # Small delay
        buffer.record("ws-1")
        new_ts = buffer._buffer["ws-1"]

        assert new_ts > old_ts

    def test_record_throttles_frequent_calls(self):
        """record() ignores calls within throttle window."""
        buffer = ActivityBuffer(throttle_sec=1.0)

        buffer.record("ws-1")
        first_ts = buffer._buffer["ws-1"]

        # Immediate second call should be throttled
        buffer.record("ws-1")
        assert buffer._buffer["ws-1"] == first_ts  # Timestamp unchanged

    def test_record_multiple_workspaces(self):
        """record() handles multiple workspaces."""
        buffer = ActivityBuffer()

        buffer.record("ws-1")
        buffer.record("ws-2")
        buffer.record("ws-3")

        assert len(buffer._buffer) == 3
        assert "ws-1" in buffer._buffer
        assert "ws-2" in buffer._buffer
        assert "ws-3" in buffer._buffer

    def test_pending_count(self):
        """pending_count returns buffer size."""
        buffer = ActivityBuffer()

        assert buffer.pending_count == 0

        buffer.record("ws-1")
        assert buffer.pending_count == 1

        buffer.record("ws-2")
        assert buffer.pending_count == 2

    async def test_flush_empty_buffer(self):
        """flush() returns 0 for empty buffer."""
        buffer = ActivityBuffer()
        mock_store = AsyncMock(spec=ActivityStore)

        count = await buffer.flush(mock_store)

        assert count == 0
        mock_store.mset.assert_not_called()

    async def test_flush_sends_to_store(self):
        """flush() sends buffer to ActivityStore via mset."""
        buffer = ActivityBuffer()
        mock_store = AsyncMock(spec=ActivityStore)
        mock_store.mset = AsyncMock()

        buffer.record("ws-1")
        buffer.record("ws-2")

        count = await buffer.flush(mock_store)

        assert count == 2
        mock_store.mset.assert_called_once()

        # Check mset receives ws_id -> timestamp mapping
        call_args = mock_store.mset.call_args[0][0]
        assert "ws-1" in call_args
        assert "ws-2" in call_args

    async def test_flush_clears_buffer(self):
        """flush() clears buffer after successful send."""
        buffer = ActivityBuffer()
        mock_store = AsyncMock(spec=ActivityStore)
        mock_store.mset = AsyncMock()

        buffer.record("ws-1")
        await buffer.flush(mock_store)

        assert buffer.pending_count == 0

    async def test_flush_restores_on_error(self):
        """flush() restores buffer on Redis error."""
        buffer = ActivityBuffer()
        mock_store = AsyncMock(spec=ActivityStore)
        mock_store.mset.side_effect = redis.RedisError("Connection failed")

        buffer.record("ws-1")
        buffer.record("ws-2")

        count = await buffer.flush(mock_store)

        assert count == 0
        # Buffer should be restored
        assert buffer.pending_count == 2

    async def test_flush_does_not_overwrite_new_records(self):
        """flush() does not overwrite new records on restore."""
        buffer = ActivityBuffer(throttle_sec=0)  # Disable throttling for this test
        mock_store = AsyncMock(spec=ActivityStore)

        # Original records
        buffer.record("ws-1")
        old_ts = buffer._buffer["ws-1"]

        # Simulate: flush starts, takes snapshot
        async def slow_mset(*args, **kwargs):
            # During flush, new record arrives
            time.sleep(0.01)  # Ensure timestamp is different
            buffer.record("ws-1")  # Update ws-1 with new timestamp
            raise redis.RedisError("Connection failed")

        mock_store.mset.side_effect = slow_mset

        await buffer.flush(mock_store)

        # New timestamp should be preserved, not overwritten by old
        assert buffer._buffer["ws-1"] > old_ts


class TestActivityStore:
    """ActivityStore unit tests."""

    async def test_mset_empty(self):
        """mset() does nothing for empty dict."""
        mock_redis = AsyncMock(spec=redis.Redis)
        store = ActivityStore(mock_redis)

        await store.mset({})

        mock_redis.mset.assert_not_called()

    async def test_mset_with_data(self):
        """mset() sets keys with prefix."""
        mock_redis = AsyncMock(spec=redis.Redis)
        mock_redis.mset = AsyncMock()
        store = ActivityStore(mock_redis)

        await store.mset({"ws-1": 1704067200.0, "ws-2": 1704067300.0})

        mock_redis.mset.assert_called_once()
        call_args = mock_redis.mset.call_args[0][0]
        assert "last_access:ws-1" in call_args
        assert "last_access:ws-2" in call_args
        assert call_args["last_access:ws-1"] == "1704067200.0"

    async def test_scan_all_empty(self):
        """scan_all() returns empty dict when no keys."""
        mock_redis = AsyncMock(spec=redis.Redis)

        async def empty_scan(*args, **kwargs):
            return
            yield  # Make it an async generator that yields nothing

        mock_redis.scan_iter = MagicMock(return_value=empty_scan())
        store = ActivityStore(mock_redis)

        result = await store.scan_all()

        assert result == {}

    async def test_scan_all_with_keys(self):
        """scan_all() returns workspace_id -> timestamp mapping."""
        mock_redis = AsyncMock(spec=redis.Redis)

        # Mock scan_iter to return keys
        async def mock_scan(*args, **kwargs):
            yield b"last_access:ws-1"
            yield b"last_access:ws-2"

        mock_redis.scan_iter = MagicMock(return_value=mock_scan())
        mock_redis.get = AsyncMock(side_effect=[b"1704067200.0", b"1704067300.0"])
        store = ActivityStore(mock_redis)

        result = await store.scan_all()

        assert result == {
            "ws-1": 1704067200.0,
            "ws-2": 1704067300.0,
        }

    async def test_delete_empty_list(self):
        """delete() returns 0 for empty list."""
        mock_redis = AsyncMock(spec=redis.Redis)
        store = ActivityStore(mock_redis)

        count = await store.delete([])

        assert count == 0
        mock_redis.delete.assert_not_called()

    async def test_delete_keys(self):
        """delete() deletes keys with prefix."""
        mock_redis = AsyncMock(spec=redis.Redis)
        mock_redis.delete = AsyncMock(return_value=2)
        store = ActivityStore(mock_redis)

        count = await store.delete(["ws-1", "ws-2"])

        assert count == 2
        mock_redis.delete.assert_called_once_with(
            "last_access:ws-1",
            "last_access:ws-2",
        )


class TestGetActivityBuffer:
    """get_activity_buffer() singleton tests."""

    def test_returns_same_instance(self):
        """Returns same buffer instance (singleton)."""
        buffer1 = get_activity_buffer()
        buffer2 = get_activity_buffer()

        assert buffer1 is buffer2
