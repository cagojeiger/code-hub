"""Tests for judge.py - Phase calculation and invariant checking.

Reference: docs/architecture_v2/wc-judge.md
"""

import pytest

from codehub.control.coordinator.judge import (
    JudgeInput,
    JudgeOutput,
    check_invariants,
    judge,
)
from codehub.core.domain.conditions import ConditionInput
from codehub.core.domain.workspace import (
    ArchiveReason,
    ErrorReason,
    Phase,
)


class TestBasicPhaseCalculation:
    """기본 상태 계산 테스트 (JDG-001 ~ JDG-004)."""

    def test_jdg_001_all_false_returns_pending(self):
        """JDG-001: 모든 조건 False → PENDING."""
        cond = ConditionInput(
            container_ready=False,
            volume_ready=False,
            archive_ready=False,
        )
        input = JudgeInput(conditions=cond, deleted_at=False)

        output = judge(input)

        assert output.phase == Phase.PENDING
        assert output.healthy is True

    def test_jdg_002_archive_only_returns_archived(self):
        """JDG-002: archive만 True → ARCHIVED."""
        cond = ConditionInput(
            container_ready=False,
            volume_ready=False,
            archive_ready=True,
        )
        input = JudgeInput(conditions=cond, deleted_at=False)

        output = judge(input)

        assert output.phase == Phase.ARCHIVED
        assert output.healthy is True

    def test_jdg_003_volume_only_returns_standby(self):
        """JDG-003: volume만 True → STANDBY."""
        cond = ConditionInput(
            container_ready=False,
            volume_ready=True,
            archive_ready=False,
        )
        input = JudgeInput(conditions=cond, deleted_at=False)

        output = judge(input)

        assert output.phase == Phase.STANDBY
        assert output.healthy is True

    def test_jdg_004_container_and_volume_returns_running(self):
        """JDG-004: container + volume → RUNNING."""
        cond = ConditionInput(
            container_ready=True,
            volume_ready=True,
            archive_ready=False,
        )
        input = JudgeInput(conditions=cond, deleted_at=False)

        output = judge(input)

        assert output.phase == Phase.RUNNING
        assert output.healthy is True


class TestInvariantViolation:
    """불변식 위반 테스트 (JDG-005, JDG-008)."""

    def test_jdg_005_container_without_volume_returns_error(self):
        """JDG-005: container=T, volume=F → ERROR (ContainerWithoutVolume)."""
        cond = ConditionInput(
            container_ready=True,
            volume_ready=False,
            archive_ready=False,
        )
        input = JudgeInput(conditions=cond, deleted_at=False)

        output = judge(input)

        assert output.phase == Phase.ERROR
        assert output.healthy is False
        assert output.error_reason == ErrorReason.CONTAINER_WITHOUT_VOLUME

    def test_jdg_008_archive_corrupted_returns_error(self):
        """JDG-008: archive.reason=ArchiveCorrupted → ERROR."""
        cond = ConditionInput(
            container_ready=False,
            volume_ready=False,
            archive_ready=False,
            archive_reason=ArchiveReason.ARCHIVE_CORRUPTED.value,
        )
        input = JudgeInput(conditions=cond, deleted_at=False)

        output = judge(input)

        assert output.phase == Phase.ERROR
        assert output.healthy is False
        assert output.error_reason == ErrorReason.ARCHIVE_CORRUPTED

    def test_archive_expired_returns_error(self):
        """archive.reason=ArchiveExpired → ERROR."""
        cond = ConditionInput(
            container_ready=False,
            volume_ready=False,
            archive_ready=False,
            archive_reason=ArchiveReason.ARCHIVE_EXPIRED.value,
        )
        input = JudgeInput(conditions=cond, deleted_at=False)

        output = judge(input)

        assert output.phase == Phase.ERROR
        assert output.healthy is False

    def test_archive_not_found_returns_error(self):
        """archive.reason=ArchiveNotFound → ERROR."""
        cond = ConditionInput(
            container_ready=False,
            volume_ready=False,
            archive_ready=False,
            archive_reason=ArchiveReason.ARCHIVE_NOT_FOUND.value,
        )
        input = JudgeInput(conditions=cond, deleted_at=False)

        output = judge(input)

        assert output.phase == Phase.ERROR
        assert output.healthy is False


class TestDeletionHandling:
    """삭제 처리 테스트 (JDG-006, JDG-007)."""

    def test_jdg_006_deleted_with_resources_returns_deleting(self):
        """JDG-006: deleted_at + resources → DELETING."""
        cond = ConditionInput(
            container_ready=True,
            volume_ready=True,
            archive_ready=False,
        )
        input = JudgeInput(conditions=cond, deleted_at=True)

        output = judge(input)

        assert output.phase == Phase.DELETING
        assert output.healthy is True

    def test_jdg_007_deleted_without_resources_returns_deleted(self):
        """JDG-007: deleted_at + no resources → DELETED."""
        cond = ConditionInput(
            container_ready=False,
            volume_ready=False,
            archive_ready=False,
        )
        input = JudgeInput(conditions=cond, deleted_at=True)

        output = judge(input)

        assert output.phase == Phase.DELETED
        assert output.healthy is True


class TestFallback:
    """Fallback 테스트 (JDG-009 ~ JDG-011)."""

    def test_jdg_009_transient_failure_with_archive_key_returns_archived(self):
        """JDG-009: 일시 장애 + archive_key → ARCHIVED (Fallback)."""
        cond = ConditionInput(
            container_ready=False,
            volume_ready=False,
            archive_ready=False,
            archive_reason=ArchiveReason.ARCHIVE_UNREACHABLE.value,
        )
        input = JudgeInput(
            conditions=cond,
            deleted_at=False,
            archive_key="ws-123/op-456/home.tar.zst",
        )

        output = judge(input)

        assert output.phase == Phase.ARCHIVED
        assert output.healthy is True

    def test_jdg_010_terminal_failure_with_archive_key_returns_error(self):
        """JDG-010: 터미널 오류 + archive_key → ERROR."""
        cond = ConditionInput(
            container_ready=False,
            volume_ready=False,
            archive_ready=False,
            archive_reason=ArchiveReason.ARCHIVE_NOT_FOUND.value,
        )
        input = JudgeInput(
            conditions=cond,
            deleted_at=False,
            archive_key="ws-123/op-456/home.tar.zst",
        )

        output = judge(input)

        assert output.phase == Phase.ERROR
        assert output.healthy is False

    def test_jdg_011_transient_failure_without_archive_key_returns_pending(self):
        """JDG-011: 일시 장애 + no archive_key → PENDING."""
        cond = ConditionInput(
            container_ready=False,
            volume_ready=False,
            archive_ready=False,
            archive_reason=ArchiveReason.ARCHIVE_UNREACHABLE.value,
        )
        input = JudgeInput(conditions=cond, deleted_at=False, archive_key=None)

        output = judge(input)

        assert output.phase == Phase.PENDING
        assert output.healthy is True

    def test_timeout_with_archive_key_returns_archived(self):
        """일시 장애 (Timeout) + archive_key → ARCHIVED (Fallback)."""
        cond = ConditionInput(
            container_ready=False,
            volume_ready=False,
            archive_ready=False,
            archive_reason=ArchiveReason.ARCHIVE_TIMEOUT.value,
        )
        input = JudgeInput(
            conditions=cond,
            deleted_at=False,
            archive_key="ws-123/op-456/home.tar.zst",
        )

        output = judge(input)

        assert output.phase == Phase.ARCHIVED
        assert output.healthy is True


class TestOrderPriority:
    """판단 순서 검증 (JDG-ORD-001 ~ JDG-ORD-003)."""

    def test_jdg_ord_001_deleted_at_takes_priority_over_resources(self):
        """JDG-ORD-001: deleted_at > resources."""
        # deleted_at + RUNNING 상태 리소스 → DELETING (not RUNNING)
        cond = ConditionInput(
            container_ready=True,
            volume_ready=True,
            archive_ready=True,
        )
        input = JudgeInput(conditions=cond, deleted_at=True)

        output = judge(input)

        assert output.phase == Phase.DELETING

    def test_jdg_ord_002_healthy_takes_priority_over_resources(self):
        """JDG-ORD-002: healthy > resources."""
        # 불변식 위반 + volume 있음 → ERROR (not STANDBY)
        cond = ConditionInput(
            container_ready=True,
            volume_ready=False,  # 불변식 위반
            archive_ready=True,
        )
        input = JudgeInput(conditions=cond, deleted_at=False)

        output = judge(input)

        assert output.phase == Phase.ERROR

    def test_jdg_ord_003_higher_level_resource_takes_priority(self):
        """JDG-ORD-003: 구체(volume) > 일반(archive)."""
        # volume + archive → STANDBY (not ARCHIVED)
        cond = ConditionInput(
            container_ready=False,
            volume_ready=True,
            archive_ready=True,
        )
        input = JudgeInput(conditions=cond, deleted_at=False)

        output = judge(input)

        assert output.phase == Phase.STANDBY


class TestCheckInvariants:
    """check_invariants 함수 테스트."""

    def test_healthy_returns_true_and_none(self):
        """정상 상태 → (True, None)."""
        cond = ConditionInput(
            container_ready=True,
            volume_ready=True,
            archive_ready=False,
        )

        healthy, error_reason = check_invariants(cond)

        assert healthy is True
        assert error_reason is None

    def test_container_without_volume_returns_false(self):
        """Container without Volume → (False, CONTAINER_WITHOUT_VOLUME)."""
        cond = ConditionInput(
            container_ready=True,
            volume_ready=False,
        )

        healthy, error_reason = check_invariants(cond)

        assert healthy is False
        assert error_reason == ErrorReason.CONTAINER_WITHOUT_VOLUME

    def test_archive_corrupted_returns_false(self):
        """Archive Corrupted → (False, ARCHIVE_CORRUPTED)."""
        cond = ConditionInput(
            container_ready=False,
            volume_ready=False,
            archive_reason=ArchiveReason.ARCHIVE_CORRUPTED.value,
        )

        healthy, error_reason = check_invariants(cond)

        assert healthy is False
        assert error_reason == ErrorReason.ARCHIVE_CORRUPTED


class TestConditionInputFromConditions:
    """ConditionInput.from_conditions 테스트."""

    def test_from_empty_conditions(self):
        """빈 conditions → 모든 필드 기본값."""
        cond = ConditionInput.from_conditions({})

        assert cond.container_ready is False
        assert cond.volume_ready is False
        assert cond.archive_ready is False
        assert cond.archive_reason is None

    def test_from_full_conditions(self):
        """모든 조건 True인 conditions."""
        conditions = {
            "infra.container_ready": {"status": "True", "reason": "ContainerRunning"},
            "storage.volume_ready": {"status": "True", "reason": "VolumeMounted"},
            "storage.archive_ready": {"status": "True", "reason": "ArchiveUploaded"},
        }

        cond = ConditionInput.from_conditions(conditions)

        assert cond.container_ready is True
        assert cond.volume_ready is True
        assert cond.archive_ready is True
        assert cond.archive_reason == "ArchiveUploaded"

    def test_from_conditions_with_archive_error(self):
        """Archive 오류 상태 conditions."""
        conditions = {
            "storage.archive_ready": {
                "status": "False",
                "reason": "ArchiveCorrupted",
            },
        }

        cond = ConditionInput.from_conditions(conditions)

        assert cond.archive_ready is False
        assert cond.archive_reason == "ArchiveCorrupted"
