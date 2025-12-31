# Error Handling (M2)

> [README.md](./README.md)로 돌아가기

---

## 핵심 원칙

1. **ERROR는 fallback 상태** - 모든 예외는 궁극적으로 ERROR로 수렴
2. **구조화된 에러 정보** - reason + message + context
3. **자동 재시도 후 관리자 개입** - 3회 재시도, 초과 시 알림
4. **GC 보호** - ERROR 상태 workspace의 archive는 삭제하지 않음

---

## ErrorInfo 구조

```python
from typing import Literal
from dataclasses import dataclass
from datetime import datetime

ErrorReason = Literal[
    "Mismatch",      # 상태 불일치 (expected vs actual)
    "Unreachable",   # API/인프라 호출 실패
    "ActionFailed",  # 작업 실행 실패 (archive, restore 등)
    "Timeout",       # 작업 시간 초과
    "DataLost",      # 데이터 손실/손상 (관리자 개입 필요)
]

@dataclass
class ErrorInfo:
    reason: ErrorReason   # 에러 유형
    message: str          # 사람이 읽는 메시지
    context: dict         # reason별 상세 정보
    occurred_at: datetime # 발생 시간
```

### 설계 결정

| 항목 | 결정 | 이유 |
|------|------|------|
| 스키마 | 공통 필드 + context | 확장성, DB 변경 없이 reason 추가 가능 |
| reason 타입 | Literal (하이브리드) | 타입 안전 + 문자열 저장 + IDE 자동완성 |
| context | JSON dict | reason별 다른 구조, 자유로운 확장 |

---

## ErrorReason 정의

### Mismatch - 상태 불일치

관측(check) 결과가 예상과 다른 경우.

```python
ErrorInfo(
    reason="Mismatch",
    message="Volume should exist but not found",
    context={
        "expected": "volume_exists=True",
        "actual": "volume_exists=False"
    },
    occurred_at=datetime.utcnow()
)
```

| 필드 | 설명 |
|------|------|
| expected | DB/operation이 기대하는 상태 |
| actual | 관측된 실제 상태 |

### Unreachable - API 호출 실패

외부 시스템(K8s API, S3 등) 접근 실패.

```python
ErrorInfo(
    reason="Unreachable",
    message="K8s API connection refused",
    context={
        "endpoint": "k8s-api",
        "status_code": 503,
        "error": "connection refused"
    },
    occurred_at=datetime.utcnow()
)
```

| 필드 | 설명 |
|------|------|
| endpoint | 호출 대상 (k8s-api, s3, docker) |
| status_code | HTTP 상태 코드 (있는 경우) |
| error | 에러 메시지 |

### ActionFailed - 작업 실패

Actuator(provision, archive 등) 실행 실패.

```python
ErrorInfo(
    reason="ActionFailed",
    message="Archive job failed with exit code 1",
    context={
        "action": "archive",
        "exit_code": 1,
        "stderr": "disk full"
    },
    occurred_at=datetime.utcnow()
)
```

| 필드 | 설명 |
|------|------|
| action | 실패한 작업 (provision, restore, archive, delete_volume, start, delete) |
| exit_code | Job exit code (있는 경우) |
| stderr | 에러 출력 (있는 경우) |

### Timeout - 시간 초과

operation이 제한 시간 내 완료되지 않음.

```python
ErrorInfo(
    reason="Timeout",
    message="Operation ARCHIVING timed out after 1800s",
    context={
        "operation": "ARCHIVING",
        "elapsed_seconds": 1800,
        "limit_seconds": 1800
    },
    occurred_at=datetime.utcnow()
)
```

| 필드 | 설명 |
|------|------|
| operation | 타임아웃된 operation |
| elapsed_seconds | 경과 시간 |
| limit_seconds | 제한 시간 |

### DataLost - 데이터 손실

복구 불가능한 데이터 손상. 관리자 즉시 개입 필요.

```python
ErrorInfo(
    reason="DataLost",
    message="Archive checksum mismatch",
    context={
        "archive_key": "archives/abc/123/home.tar.gz",
        "detail": "checksum mismatch"
    },
    occurred_at=datetime.utcnow()
)
```

| 필드 | 설명 |
|------|------|
| archive_key | 손상된 archive 경로 |
| detail | 손상 상세 (checksum mismatch, file not found 등) |

---

## 에러 처리 정책

### 재시도 정책

| reason | 재시도 | 관리자 호출 | 근거 |
|--------|--------|------------|------|
| Mismatch | 3회 | 3회 초과 시 | 일시적 불일치 가능 |
| Unreachable | 3회 | 3회 초과 시 | 네트워크 일시 장애 |
| ActionFailed | 3회 | 3회 초과 시 | 일시적 리소스 부족 |
| Timeout | 1회 | 즉시 | 반복해도 동일 결과 예상 |
| DataLost | 0회 | 즉시 | 재시도 무의미 |

### 재시도 로직

```python
async def handle_error(ws: Workspace, error_info: ErrorInfo):
    """에러 발생 시 재시도 또는 ERROR 전환"""

    max_retries = get_max_retries(error_info.reason)

    if ws.error_count < max_retries:
        # 재시도: error_count 증가, operation 유지
        await db.bump_error_count(ws.id, error_info)
        # 다음 루프에서 재시도
    else:
        # ERROR 전환
        await transition_to_error(ws, error_info)
        # 관리자 알림
        await notify_admin(ws, error_info)

def get_max_retries(reason: ErrorReason) -> int:
    return {
        "Mismatch": 3,
        "Unreachable": 3,
        "ActionFailed": 3,
        "Timeout": 1,
        "DataLost": 0,
    }[reason]
```

### GC 동작

**ERROR 상태 workspace는 GC에서 보호됩니다.**

| 상태 | GC 동작 | 이유 |
|------|---------|------|
| ERROR | pass (삭제 안 함) | 복구 시 archive 필요할 수 있음 |
| DELETED | 삭제 대상 | soft-delete된 workspace |

> **상세**: [storage-gc.md](./storage-gc.md) 참조

---

## ERROR 전환

### transition_to_error()

```python
async def transition_to_error(ws: Workspace, error_info: ErrorInfo):
    """operation 실패 시 ERROR로 전환

    Note: op_id는 유지됨 → GC 보호, 복구 시 재사용
    """
    await update_workspace(
        ws.id,
        previous_status=ws.status,
        status="ERROR",
        operation="NONE",
        error_info=asdict(error_info),  # JSON 저장
        error_count=ws.error_count + 1
        # op_id: 유지 (명시적으로 변경하지 않음)
    )
```

### ERROR 관련 컬럼

| 컬럼 | 타입 | 설명 |
|------|------|------|
| previous_status | str | ERROR 전 상태 (복구 시 사용) |
| error_info | JSON | ErrorInfo (reason, message, context, occurred_at) |
| error_count | int | 연속 실패 횟수 |
| op_id | str | ERROR에서도 유지 (GC 보호) |

---

## ERROR 복구

### recover_from_error()

```python
async def recover_from_error(ws: Workspace):
    """ERROR 상태에서 복구 (관리자 또는 자동)

    Note: op_id는 유지 (이미 업로드된 archive 재사용)
    """
    if ws.status != "ERROR":
        return

    await update_workspace(
        ws.id,
        status=ws.previous_status,  # STANDBY, PENDING 등
        previous_status=None,
        operation="NONE",
        error_info=None,
        error_count=0
        # op_id: 유지
    )
    # Reconciler가 다음 루프에서 다시 시도
```

### 복구 시나리오

| reason | 복구 방법 |
|--------|----------|
| Mismatch | 자동 복구 시도, 실패 시 관리자 확인 |
| Unreachable | 인프라 복구 후 자동 재시도 |
| ActionFailed | 원인 해결 후 수동 복구 트리거 |
| Timeout | 리소스 확장 또는 timeout 조정 후 재시도 |
| DataLost | 백업 복원 또는 archive_key NULL 처리 |

---

## 에러 코드 매핑

기존 에러 코드를 ErrorReason으로 매핑합니다.

| 기존 코드 | ErrorReason | context 예시 |
|----------|-------------|-------------|
| ARCHIVE_NOT_FOUND | DataLost | `{"detail": "archive not found"}` |
| S3_ACCESS_ERROR | Unreachable | `{"endpoint": "s3"}` |
| CHECKSUM_MISMATCH | DataLost | `{"detail": "checksum mismatch"}` |
| TAR_EXTRACT_FAILED | ActionFailed | `{"action": "restore"}` |
| VOLUME_CREATE_FAILED | ActionFailed | `{"action": "provision"}` |
| CONTAINER_START_FAILED | ActionFailed | `{"action": "start"}` |
| K8S_API_ERROR | Unreachable | `{"endpoint": "k8s-api"}` |

---

## 참조

- [states.md](./states.md) - ERROR 상태 정의
- [reconciler.md](./reconciler.md) - 에러 처리 로직, timeout
- [storage.md](./storage.md) - Operation 플로우, 에러 처리
- [storage-gc.md](./storage-gc.md) - ERROR 상태 GC 보호
