"""Condition status types for workspace state management.

Reference: docs/spec_v2/03-schema.md#conditions-jsonb-구조
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ConditionStatus(BaseModel):
    """Condition status (K8s pattern).

    status is string "True"/"False" for type safety (K8s convention).
    """

    status: Literal["True", "False"]
    reason: str  # CamelCase reason
    message: str  # Human-readable message
    last_transition_time: datetime

    def is_true(self) -> bool:
        """Check if condition status is True."""
        return self.status == "True"

    def to_dict(self) -> dict:
        """Convert to dict for JSONB storage."""
        return {
            "status": self.status,
            "reason": self.reason,
            "message": self.message,
            "last_transition_time": self.last_transition_time.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConditionStatus":
        """Create from dict (JSONB storage)."""
        return cls(
            status=data.get("status", "False"),
            reason=data.get("reason", "Unknown"),
            message=data.get("message", ""),
            last_transition_time=datetime.fromisoformat(
                data.get("last_transition_time", datetime.now().isoformat())
            ),
        )


class ConditionInput(BaseModel):
    """Judge 입력용 condition 요약.

    Reference: docs/architecture_v2/wc-judge.md
    """

    container_ready: bool = False
    volume_ready: bool = False
    archive_ready: bool = False
    archive_reason: str | None = None

    model_config = {"frozen": True}

    @classmethod
    def from_conditions(cls, conditions: dict) -> "ConditionInput":
        """Raw conditions dict에서 생성.

        Args:
            conditions: DB에서 읽은 raw conditions JSONB

        Returns:
            ConditionInput 인스턴스
        """
        container = conditions.get("infra.container_ready", {})
        volume = conditions.get("storage.volume_ready", {})
        archive = conditions.get("storage.archive_ready", {})

        return cls(
            container_ready=container.get("status") == "True",
            volume_ready=volume.get("status") == "True",
            archive_ready=archive.get("status") == "True",
            archive_reason=archive.get("reason"),
        )
