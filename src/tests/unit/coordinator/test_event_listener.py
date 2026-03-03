import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from codehub.control.coordinator.event_listener import EventListener
from codehub.infra.redis_pubsub import ChannelPublisher


class TestEventListenerInit:
    def test_init_with_publisher(
        self,
        mock_redis_client: AsyncMock,
        mock_publisher: AsyncMock,
    ) -> None:
        with patch("codehub.control.coordinator.event_listener.ChannelPublisher") as mock_class:
            listener = EventListener("postgresql://localhost:5432/codehub", mock_redis_client, mock_publisher)

        assert listener._publisher is mock_publisher
        mock_class.assert_not_called()

    def test_init_creates_publisher_from_redis(self, mock_redis_client: AsyncMock) -> None:
        created_publisher = AsyncMock(spec=ChannelPublisher)
        with patch(
            "codehub.control.coordinator.event_listener.ChannelPublisher",
            return_value=created_publisher,
        ) as mock_class:
            listener = EventListener("postgresql://localhost:5432/codehub", mock_redis_client)

        assert listener._publisher is created_publisher
        mock_class.assert_called_once_with(mock_redis_client)


class TestStop:
    def test_stop_sets_running_false(self, event_listener: EventListener) -> None:
        event_listener._running = True

        event_listener.stop()

        assert event_listener._running is False


class TestDispatch:
    async def test_dispatch_routes_sse_channel(self, event_listener: EventListener) -> None:
        event_listener._handle_sse = AsyncMock()
        event_listener._handle_wake = AsyncMock()

        metric = MagicMock()
        with patch(
            "codehub.control.coordinator.event_listener.EVENT_NOTIFY_RECEIVED_TOTAL",
            metric,
        ):
            await event_listener._dispatch(EventListener.CHANNEL_SSE, '{"id":"ws-1"}')

        event_listener._handle_sse.assert_called_once_with('{"id":"ws-1"}')
        event_listener._handle_wake.assert_not_called()
        metric.labels.assert_called_once_with(channel=EventListener.CHANNEL_SSE)
        metric.labels.return_value.inc.assert_called_once()

    async def test_dispatch_routes_wake_channel(self, event_listener: EventListener) -> None:
        event_listener._handle_sse = AsyncMock()
        event_listener._handle_wake = AsyncMock()

        metric = MagicMock()
        with patch(
            "codehub.control.coordinator.event_listener.EVENT_NOTIFY_RECEIVED_TOTAL",
            metric,
        ):
            await event_listener._dispatch(EventListener.CHANNEL_WAKE, "")

        event_listener._handle_wake.assert_called_once_with()
        event_listener._handle_sse.assert_not_called()
        metric.labels.assert_called_once_with(channel=EventListener.CHANNEL_WAKE)
        metric.labels.return_value.inc.assert_called_once()

    async def test_dispatch_unknown_channel_no_error(self, event_listener: EventListener) -> None:
        event_listener._handle_sse = AsyncMock()
        event_listener._handle_wake = AsyncMock()

        metric = MagicMock()
        with patch(
            "codehub.control.coordinator.event_listener.EVENT_NOTIFY_RECEIVED_TOTAL",
            metric,
        ):
            await event_listener._dispatch("unknown", "payload")

        event_listener._handle_sse.assert_not_called()
        event_listener._handle_wake.assert_not_called()
        metric.labels.assert_called_once_with(channel="unknown")
        metric.labels.return_value.inc.assert_called_once()

    async def test_dispatch_handler_exception_caught(self, event_listener: EventListener) -> None:
        event_listener._handle_sse = AsyncMock(side_effect=RuntimeError("boom"))

        metric = MagicMock()
        with (
            patch("codehub.control.coordinator.event_listener.EVENT_NOTIFY_RECEIVED_TOTAL", metric),
            patch("codehub.control.coordinator.event_listener.logger.exception") as mock_log,
        ):
            await event_listener._dispatch(EventListener.CHANNEL_SSE, "payload")

        mock_log.assert_called_once()


class TestHandleSse:
    async def test_handle_sse_valid_payload(self, event_listener: EventListener) -> None:
        workspace = {"id": "ws-1", "owner_user_id": "u-1", "deleted_at": None}
        event_listener._fetch_workspace = AsyncMock(return_value=workspace)
        event_listener._publisher.publish = AsyncMock(return_value=2)

        sse_metric = MagicMock()
        with (
            patch(
                "codehub.control.coordinator.event_listener._channel_config",
                SimpleNamespace(sse_prefix="codehub:sse", wake_prefix="codehub:wake"),
            ),
            patch("codehub.control.coordinator.event_listener.EVENT_SSE_PUBLISHED_TOTAL", sse_metric),
        ):
            await event_listener._handle_sse('{"id": "ws-1", "owner_user_id": "u-1"}')

        event_listener._fetch_workspace.assert_called_once_with("ws-1")
        event_listener._publisher.publish.assert_called_once()
        publish_channel, publish_payload = event_listener._publisher.publish.call_args.args
        assert publish_channel == "codehub:sse:u-1"
        assert '"id": "ws-1"' in publish_payload
        sse_metric.inc.assert_called_once()

    async def test_handle_sse_invalid_json(self, event_listener: EventListener) -> None:
        errors_metric = MagicMock()
        with (
            patch("codehub.control.coordinator.event_listener.EVENT_ERRORS_TOTAL", errors_metric),
            patch("codehub.control.coordinator.event_listener.logger.warning") as mock_log,
        ):
            await event_listener._handle_sse("not-json")

        errors_metric.labels.assert_called_once_with(operation="sse")
        errors_metric.labels.return_value.inc.assert_called_once()
        mock_log.assert_called_once()

    async def test_handle_sse_missing_id_field(self, event_listener: EventListener) -> None:
        event_listener._fetch_workspace = AsyncMock()
        event_listener._publisher.publish = AsyncMock()

        await event_listener._handle_sse('{"owner_user_id": "u-1"}')

        event_listener._fetch_workspace.assert_not_called()
        event_listener._publisher.publish.assert_not_called()

    async def test_handle_sse_missing_owner_field(self, event_listener: EventListener) -> None:
        event_listener._fetch_workspace = AsyncMock()
        event_listener._publisher.publish = AsyncMock()

        await event_listener._handle_sse('{"id": "ws-1"}')

        event_listener._fetch_workspace.assert_not_called()
        event_listener._publisher.publish.assert_not_called()

    async def test_handle_sse_workspace_not_found(self, event_listener: EventListener) -> None:
        event_listener._fetch_workspace = AsyncMock(return_value=None)
        event_listener._publisher.publish = AsyncMock()

        await event_listener._handle_sse('{"id": "ws-1", "owner_user_id": "u-1"}')

        event_listener._fetch_workspace.assert_called_once_with("ws-1")
        event_listener._publisher.publish.assert_not_called()

    async def test_handle_sse_publishes_to_correct_channel(self, event_listener: EventListener) -> None:
        event_listener._fetch_workspace = AsyncMock(
            return_value={"id": "ws-2", "owner_user_id": "user-42", "deleted_at": None}
        )
        event_listener._publisher.publish = AsyncMock(return_value=1)

        with patch(
            "codehub.control.coordinator.event_listener._channel_config",
            SimpleNamespace(sse_prefix="app:sse", wake_prefix="app:wake"),
        ):
            await event_listener._handle_sse('{"id": "ws-2", "owner_user_id": "user-42"}')

        assert event_listener._publisher.publish.call_args.args[0] == "app:sse:user-42"

    async def test_handle_sse_publish_exception_caught(self, event_listener: EventListener) -> None:
        event_listener._fetch_workspace = AsyncMock(
            return_value={"id": "ws-1", "owner_user_id": "u-1", "deleted_at": None}
        )
        event_listener._publisher.publish = AsyncMock(side_effect=RuntimeError("redis down"))

        errors_metric = MagicMock()
        with (
            patch(
                "codehub.control.coordinator.event_listener._channel_config",
                SimpleNamespace(sse_prefix="codehub:sse", wake_prefix="codehub:wake"),
            ),
            patch("codehub.control.coordinator.event_listener.EVENT_ERRORS_TOTAL", errors_metric),
            patch("codehub.control.coordinator.event_listener.logger.exception") as mock_log,
        ):
            await event_listener._handle_sse('{"id": "ws-1", "owner_user_id": "u-1"}')

        errors_metric.labels.assert_called_once_with(operation="sse")
        errors_metric.labels.return_value.inc.assert_called_once()
        mock_log.assert_called_once()


class TestFetchWorkspace:
    async def test_fetch_workspace_success(self, event_listener: EventListener) -> None:
        row = (
            "ws-1",
            "u-1",
            "name",
            "desc",
            "memo",
            "img",
            "RUNNING",
            "START",
            "ACTIVE",
            "archive",
            None,
            0,
            datetime(2025, 1, 1, 12, 0, 0),
            datetime(2025, 1, 2, 12, 0, 0),
            datetime(2025, 1, 3, 12, 0, 0),
            datetime(2025, 1, 4, 12, 0, 0),
            None,
        )
        result_mock = MagicMock()
        result_mock.fetchone.return_value = row
        sa_conn = AsyncMock()
        sa_conn.execute = AsyncMock(return_value=result_mock)
        event_listener._sa_conn = sa_conn

        result = await event_listener._fetch_workspace("ws-1")

        assert result is not None
        assert result["id"] == "ws-1"
        assert result["owner_user_id"] == "u-1"
        assert result["phase"] == "RUNNING"
        assert result["error_count"] == 0

    async def test_fetch_workspace_not_found(self, event_listener: EventListener) -> None:
        result_mock = MagicMock()
        result_mock.fetchone.return_value = None
        sa_conn = AsyncMock()
        sa_conn.execute = AsyncMock(return_value=result_mock)
        event_listener._sa_conn = sa_conn

        result = await event_listener._fetch_workspace("missing")

        assert result is None

    async def test_fetch_workspace_no_connection(self, event_listener: EventListener) -> None:
        event_listener._sa_conn = None

        result = await event_listener._fetch_workspace("ws-1")

        assert result is None

    async def test_fetch_workspace_datetime_conversion(self, event_listener: EventListener) -> None:
        created_at = datetime(2025, 2, 1, 1, 2, 3)
        updated_at = datetime(2025, 2, 1, 4, 5, 6)
        row = (
            "ws-1",
            "u-1",
            None,
            None,
            None,
            None,
            "IDLE",
            None,
            None,
            None,
            None,
            3,
            created_at,
            updated_at,
            None,
            None,
            None,
        )
        result_mock = MagicMock()
        result_mock.fetchone.return_value = row
        sa_conn = AsyncMock()
        sa_conn.execute = AsyncMock(return_value=result_mock)
        event_listener._sa_conn = sa_conn

        result = await event_listener._fetch_workspace("ws-1")

        assert result is not None
        assert result["created_at"] == created_at.isoformat()
        assert result["updated_at"] == updated_at.isoformat()


class TestHandleWake:
    async def test_handle_wake_publishes_both_channels(self, event_listener: EventListener) -> None:
        event_listener._publisher.publish = AsyncMock(side_effect=[3, 5])

        wake_metric = MagicMock()
        with (
            patch(
                "codehub.control.coordinator.event_listener._channel_config",
                SimpleNamespace(sse_prefix="codehub:sse", wake_prefix="codehub:wake"),
            ),
            patch("codehub.control.coordinator.event_listener.EVENT_WAKE_PUBLISHED_TOTAL", wake_metric),
        ):
            await event_listener._handle_wake()

        event_listener._publisher.publish.assert_has_calls(
            [call("codehub:wake:observer"), call("codehub:wake:wc")]
        )
        assert wake_metric.labels.call_args_list == [call(target="observer"), call(target="wc")]
        assert wake_metric.labels.return_value.inc.call_count == 2

    async def test_handle_wake_error_caught(self, event_listener: EventListener) -> None:
        event_listener._publisher.publish = AsyncMock(side_effect=RuntimeError("boom"))

        errors_metric = MagicMock()
        with (
            patch(
                "codehub.control.coordinator.event_listener._channel_config",
                SimpleNamespace(sse_prefix="codehub:sse", wake_prefix="codehub:wake"),
            ),
            patch("codehub.control.coordinator.event_listener.EVENT_ERRORS_TOTAL", errors_metric),
            patch("codehub.control.coordinator.event_listener.logger.exception") as mock_log,
        ):
            await event_listener._handle_wake()

        errors_metric.labels.assert_called_once_with(operation="wake")
        errors_metric.labels.return_value.inc.assert_called_once()
        mock_log.assert_called_once()

    async def test_handle_wake_uses_gather(self, event_listener: EventListener) -> None:
        event_listener._publisher.publish = AsyncMock(side_effect=[1, 1])

        with (
            patch(
                "codehub.control.coordinator.event_listener._channel_config",
                SimpleNamespace(sse_prefix="codehub:sse", wake_prefix="codehub:wake"),
            ),
            patch("codehub.control.coordinator.event_listener.asyncio.gather", wraps=asyncio.gather) as mock_gather,
        ):
            await event_listener._handle_wake()

        mock_gather.assert_called_once()


class TestWorkerLoop:
    async def test_worker_processes_queued_event(self, event_listener: EventListener) -> None:
        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        await queue.put(("ws_sse", "payload"))
        event_listener._event_queue = queue
        event_listener._dispatch = AsyncMock()

        worker_task = asyncio.create_task(event_listener._worker_loop())
        await queue.join()
        worker_task.cancel()
        await worker_task

        event_listener._dispatch.assert_called_once_with("ws_sse", "payload")

    async def test_worker_continues_after_dispatch_error(self, event_listener: EventListener) -> None:
        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        await queue.put(("ws_sse", "payload"))
        event_listener._event_queue = queue
        event_listener._dispatch = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("codehub.control.coordinator.event_listener.logger.exception") as mock_log:
            worker_task = asyncio.create_task(event_listener._worker_loop())
            await asyncio.sleep(0)
            worker_task.cancel()
            await worker_task

        assert event_listener._dispatch.await_count >= 1
        mock_log.assert_called()

    async def test_worker_stops_on_cancellation(self, event_listener: EventListener) -> None:
        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        event_listener._event_queue = queue
        event_listener._dispatch = AsyncMock()

        worker_task = asyncio.create_task(event_listener._worker_loop())
        await asyncio.sleep(0)
        worker_task.cancel()
        await worker_task

        event_listener._dispatch.assert_not_called()

    async def test_worker_calls_task_done(self, event_listener: EventListener) -> None:
        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        task_done = MagicMock(wraps=queue.task_done)
        queue.task_done = task_done
        await queue.put(("ws_wake", ""))
        event_listener._event_queue = queue
        event_listener._dispatch = AsyncMock()

        worker_task = asyncio.create_task(event_listener._worker_loop())
        await queue.join()
        worker_task.cancel()
        await worker_task

        task_done.assert_called_once()


class TestCloseConnections:
    async def test_close_all_connections(self, event_listener: EventListener) -> None:
        notify_conn = event_listener._notify_conn
        sa_conn = event_listener._sa_conn
        engine = event_listener._engine
        assert notify_conn is not None
        assert sa_conn is not None
        assert engine is not None

        leader_metric = MagicMock()
        with patch("codehub.control.coordinator.event_listener.COORDINATOR_IS_LEADER", leader_metric):
            await event_listener._close_connections()

        assert getattr(notify_conn.close, "await_count", 0) == 1
        assert getattr(sa_conn.close, "await_count", 0) == 1
        assert getattr(engine.dispose, "await_count", 0) == 1
        assert event_listener._notify_conn is None
        assert event_listener._sa_conn is None
        assert event_listener._engine is None

    async def test_close_with_no_connections(self, event_listener: EventListener) -> None:
        event_listener._notify_conn = None
        event_listener._sa_conn = None
        event_listener._engine = None

        leader_metric = MagicMock()
        with patch("codehub.control.coordinator.event_listener.COORDINATOR_IS_LEADER", leader_metric):
            await event_listener._close_connections()

        leader_metric.labels.return_value.set.assert_called_once_with(0)

    async def test_close_resets_leader_metric(self, event_listener: EventListener) -> None:
        event_listener._notify_conn = None
        event_listener._sa_conn = None
        event_listener._engine = None

        leader_metric = MagicMock()
        with patch("codehub.control.coordinator.event_listener.COORDINATOR_IS_LEADER", leader_metric):
            await event_listener._close_connections()

        leader_metric.labels.assert_called_once_with(coordinator="event_listener")
        leader_metric.labels.return_value.set.assert_called_once_with(0)
