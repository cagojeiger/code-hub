"""Condition input types for Judge.

Reference: docs/architecture_v2/wc-judge.md
"""

from pydantic import BaseModel


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
                {
                    "container": {"running": true, "reason": "...", ...},
                    "volume": {"exists": true, ...},
                    "archive": {"exists": true, "reason": "...", ...}
                }

        Returns:
            ConditionInput 인스턴스
        """
        container = conditions.get("container") or {}
        volume = conditions.get("volume") or {}
        archive = conditions.get("archive") or {}

        return cls(
            container_ready=container.get("running", False),
            volume_ready=volume.get("exists", False),
            archive_ready=archive.get("exists", False),
            archive_reason=archive.get("reason"),
        )
