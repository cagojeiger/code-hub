"""Unit tests for ChannelPublisher and ChannelSubscriber (PUB/SUB)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub.infra.redis_pubsub import ChannelPublisher, ChannelSubscriber


class TestChannelPublisher:
    """ChannelPublisher (PUBLISH) 테스트."""

    @pytest.mark.asyncio
    async def test_publish_sends_to_correct_channel(self) -> None:
        """publish()가 올바른 채널로 메시지 전송."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=1)

        publisher = ChannelPublisher(mock_redis)
        count = await publisher.publish("codehub:wake:ob", "wake")

        mock_redis.publish.assert_called_once_with("codehub:wake:ob", "wake")
        assert count == 1

    @pytest.mark.asyncio
    async def test_publish_with_empty_payload(self) -> None:
        """publish()가 빈 payload로 전송 (signal용)."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=2)

        publisher = ChannelPublisher(mock_redis)
        count = await publisher.publish("codehub:wake:wc")

        mock_redis.publish.assert_called_once_with("codehub:wake:wc", "")
        assert count == 2

    @pytest.mark.asyncio
    async def test_publish_with_json_payload(self) -> None:
        """publish()가 JSON payload 전송."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=1)

        publisher = ChannelPublisher(mock_redis)
        payload = '{"id": "test", "phase": "RUNNING"}'
        count = await publisher.publish("codehub:sse:user123", payload)

        mock_redis.publish.assert_called_once_with("codehub:sse:user123", payload)
        assert count == 1

    @pytest.mark.asyncio
    async def test_publish_returns_subscriber_count(self) -> None:
        """publish()가 구독자 수 반환."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=0)  # 구독자 없음

        publisher = ChannelPublisher(mock_redis)
        count = await publisher.publish("codehub:wake:gc")

        assert count == 0


class TestChannelSubscriber:
    """ChannelSubscriber (PUB/SUB) 테스트."""

    @pytest.mark.asyncio
    async def test_subscribe_creates_pubsub_and_subscribes(self) -> None:
        """subscribe()가 PubSub 생성 및 구독."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        subscriber = ChannelSubscriber(mock_redis)
        await subscriber.subscribe("codehub:wake:ob")

        mock_redis.pubsub.assert_called_once()
        mock_pubsub.subscribe.assert_called_once_with("codehub:wake:ob")
        assert subscriber.channel == "codehub:wake:ob"

    @pytest.mark.asyncio
    async def test_get_message_returns_payload_on_message(self) -> None:
        """메시지 수신 시 payload 반환."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
        mock_pubsub.get_message = AsyncMock(
            return_value={"type": "message", "data": "wake"}
        )

        subscriber = ChannelSubscriber(mock_redis)
        await subscriber.subscribe("codehub:wake:wc")

        result = await subscriber.get_message(timeout=1.0)

        assert result == "wake"
        mock_pubsub.get_message.assert_called_once_with(
            ignore_subscribe_messages=True, timeout=1.0
        )

    @pytest.mark.asyncio
    async def test_get_message_decodes_bytes(self) -> None:
        """bytes 메시지 decode."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
        mock_pubsub.get_message = AsyncMock(
            return_value={"type": "message", "data": b"test_payload"}
        )

        subscriber = ChannelSubscriber(mock_redis)
        await subscriber.subscribe("codehub:sse:user1")

        result = await subscriber.get_message(timeout=1.0)

        assert result == "test_payload"

    @pytest.mark.asyncio
    async def test_get_message_returns_none_on_no_message(self) -> None:
        """메시지 없으면 None 반환."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
        mock_pubsub.get_message = AsyncMock(return_value=None)

        subscriber = ChannelSubscriber(mock_redis)
        await subscriber.subscribe("codehub:wake:ob")

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

        subscriber = ChannelSubscriber(mock_redis)
        await subscriber.subscribe("codehub:wake:ob")

        result = await subscriber.get_message(timeout=0.5)

        # type이 "message"가 아니므로 None 반환
        assert result is None

    @pytest.mark.asyncio
    async def test_get_message_returns_none_without_subscription(self) -> None:
        """구독 없이 get_message 호출 시 None 반환."""
        mock_redis = AsyncMock()

        subscriber = ChannelSubscriber(mock_redis)
        # subscribe() 호출하지 않음

        result = await subscriber.get_message(timeout=0.5)

        assert result is None

    @pytest.mark.asyncio
    async def test_unsubscribe_closes_pubsub(self) -> None:
        """unsubscribe()가 PubSub 연결 해제."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        subscriber = ChannelSubscriber(mock_redis)
        await subscriber.subscribe("codehub:wake:ob")
        await subscriber.unsubscribe()

        mock_pubsub.unsubscribe.assert_called_once()
        mock_pubsub.close.assert_called_once()
        assert subscriber._pubsub is None
        assert subscriber.channel is None

    @pytest.mark.asyncio
    async def test_unsubscribe_handles_error_gracefully(self) -> None:
        """unsubscribe() 에러 시 graceful 처리."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
        mock_pubsub.unsubscribe = AsyncMock(side_effect=Exception("connection error"))

        subscriber = ChannelSubscriber(mock_redis)
        await subscriber.subscribe("codehub:wake:ob")

        # 에러 발생해도 예외 throw 없이 정상 종료
        await subscriber.unsubscribe()

        assert subscriber._pubsub is None
        assert subscriber.channel is None

    @pytest.mark.asyncio
    async def test_get_message_handles_error_gracefully(self) -> None:
        """get_message() 에러 시 None 반환."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
        mock_pubsub.get_message = AsyncMock(side_effect=Exception("timeout"))

        subscriber = ChannelSubscriber(mock_redis)
        await subscriber.subscribe("codehub:wake:ob")

        result = await subscriber.get_message(timeout=1.0)

        assert result is None
