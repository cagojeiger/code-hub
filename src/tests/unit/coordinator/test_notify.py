"""Unit tests for NotifyPublisher and NotifySubscriber (PUB/SUB)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub.infra.redis_pubsub import (
    NotifyPublisher,
    NotifySubscriber,
    WakeTarget,
)


class TestNotifyPublisher:
    """NotifyPublisher (PUBLISH) 테스트."""

    @pytest.mark.asyncio
    async def test_publish_sends_to_correct_channel(self) -> None:
        """publish()가 올바른 채널로 메시지 전송."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=1)

        publisher = NotifyPublisher(mock_redis)
        count = await publisher.publish(WakeTarget.OB)

        mock_redis.publish.assert_called_once_with("ob:wake", "wake")
        assert count == 1

    @pytest.mark.asyncio
    async def test_wake_ob_publishes_to_ob_channel(self) -> None:
        """wake_ob()가 ob:wake 채널로 전송."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=2)

        publisher = NotifyPublisher(mock_redis)
        count = await publisher.wake_ob()

        mock_redis.publish.assert_called_once_with("ob:wake", "wake")
        assert count == 2

    @pytest.mark.asyncio
    async def test_wake_wc_publishes_to_wc_channel(self) -> None:
        """wake_wc()가 wc:wake 채널로 전송."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=1)

        publisher = NotifyPublisher(mock_redis)
        count = await publisher.wake_wc()

        mock_redis.publish.assert_called_once_with("wc:wake", "wake")
        assert count == 1

    @pytest.mark.asyncio
    async def test_wake_gc_publishes_to_gc_channel(self) -> None:
        """wake_gc()가 gc:wake 채널로 전송."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=0)

        publisher = NotifyPublisher(mock_redis)
        count = await publisher.wake_gc()

        mock_redis.publish.assert_called_once_with("gc:wake", "wake")
        assert count == 0

    @pytest.mark.asyncio
    async def test_wake_ob_wc_publishes_in_parallel(self) -> None:
        """wake_ob_wc()가 OB와 WC에 병렬 전송."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(side_effect=[2, 1])  # OB: 2, WC: 1

        publisher = NotifyPublisher(mock_redis)
        ob_count, wc_count = await publisher.wake_ob_wc()

        assert ob_count == 2
        assert wc_count == 1
        assert mock_redis.publish.call_count == 2


class TestNotifySubscriber:
    """NotifySubscriber (PUB/SUB) 테스트."""

    @pytest.mark.asyncio
    async def test_subscribe_creates_pubsub_and_subscribes(self) -> None:
        """subscribe()가 PubSub 생성 및 구독."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        subscriber = NotifySubscriber(mock_redis)
        await subscriber.subscribe(WakeTarget.OB)

        mock_redis.pubsub.assert_called_once()
        mock_pubsub.subscribe.assert_called_once_with("ob:wake")
        assert subscriber._target == WakeTarget.OB

    @pytest.mark.asyncio
    async def test_get_message_returns_target_on_message(self) -> None:
        """메시지 수신 시 target 반환."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
        mock_pubsub.get_message = AsyncMock(
            return_value={"type": "message", "data": "wake"}
        )

        subscriber = NotifySubscriber(mock_redis)
        await subscriber.subscribe(WakeTarget.WC)

        result = await subscriber.get_message(timeout=1.0)

        assert result == "wc"
        mock_pubsub.get_message.assert_called_once_with(
            ignore_subscribe_messages=True, timeout=1.0
        )

    @pytest.mark.asyncio
    async def test_get_message_returns_none_on_no_message(self) -> None:
        """메시지 없으면 None 반환."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
        mock_pubsub.get_message = AsyncMock(return_value=None)

        subscriber = NotifySubscriber(mock_redis)
        await subscriber.subscribe(WakeTarget.OB)

        result = await subscriber.get_message(timeout=0.5)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_message_returns_none_on_subscribe_message(self) -> None:
        """subscribe 메시지 타입은 무시."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
        mock_pubsub.get_message = AsyncMock(
            return_value={"type": "subscribe", "data": 1}
        )

        subscriber = NotifySubscriber(mock_redis)
        await subscriber.subscribe(WakeTarget.OB)

        result = await subscriber.get_message(timeout=0.5)

        # type이 "message"가 아니므로 None 반환
        assert result is None

    @pytest.mark.asyncio
    async def test_get_message_returns_none_without_subscription(self) -> None:
        """구독 없이 get_message 호출 시 None 반환."""
        mock_redis = AsyncMock()

        subscriber = NotifySubscriber(mock_redis)
        # subscribe() 호출하지 않음

        result = await subscriber.get_message(timeout=0.5)

        assert result is None

    @pytest.mark.asyncio
    async def test_unsubscribe_closes_pubsub(self) -> None:
        """unsubscribe()가 PubSub 연결 해제."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        subscriber = NotifySubscriber(mock_redis)
        await subscriber.subscribe(WakeTarget.OB)
        await subscriber.unsubscribe()

        mock_pubsub.unsubscribe.assert_called_once()
        mock_pubsub.close.assert_called_once()
        assert subscriber._pubsub is None
        assert subscriber._target is None

    @pytest.mark.asyncio
    async def test_unsubscribe_handles_error_gracefully(self) -> None:
        """unsubscribe() 에러 시 graceful 처리."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
        mock_pubsub.unsubscribe = AsyncMock(side_effect=Exception("connection error"))

        subscriber = NotifySubscriber(mock_redis)
        await subscriber.subscribe(WakeTarget.OB)

        # 에러 발생해도 예외 throw 없이 정상 종료
        await subscriber.unsubscribe()

        assert subscriber._pubsub is None
        assert subscriber._target is None

    @pytest.mark.asyncio
    async def test_get_message_handles_error_gracefully(self) -> None:
        """get_message() 에러 시 None 반환."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
        mock_pubsub.get_message = AsyncMock(side_effect=Exception("timeout"))

        subscriber = NotifySubscriber(mock_redis)
        await subscriber.subscribe(WakeTarget.OB)

        result = await subscriber.get_message(timeout=1.0)

        assert result is None
