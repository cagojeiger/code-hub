# Workspace 상태 (M2)

> [README.md](./README.md)로 돌아가기

---

## 개요

본 문서는 **State SSOT** (Single Source of Truth)입니다.

| 섹션 | 역할 |
|------|------|
| **State Language** | "지금 뭐라고 부르는가?" (Phase, Operation 정의) |
| **State Machine** | "다음에 뭘 해야 하는가?" (Level, 전이 규칙) |

> **Conditions 정의**: [03-schema.md#conditions](./03-schema.md#conditions-jsonb-구조)
> **계약/규칙**: [00-contracts.md](./00-contracts.md)

---

# State Language

> **역할**: 현재 상태를 어떻게 표현하는가 (순수 데이터)

---

## Phase (요약)

Phase는 Conditions에서 계산되는 **파생 값**입니다.

| Phase | 조건 | 의미 |
|-------|------|------|
| DELETED | `deleted_at` ∧ !resources | 삭제 완료 |
| DELETING | `deleted_at` ∧ resources | 삭제 진행 중 |
| ERROR | !healthy | 정책 위반 |
| RUNNING | healthy ∧ container ∧ volume | 실행 중 |
| STANDBY | healthy ∧ volume ∧ !container | 대기 중 |
| ARCHIVED | healthy ∧ !volume ∧ archive | 보관됨 |
| PENDING | healthy ∧ !volume ∧ !archive | 초기 |

> **resources**: `volume_ready ∨ container_ready ∨ archive_ready`
> **Phase 계산**: WC가 reconcile 시 conditions를 읽어 phase 계산/저장 (인덱스용 캐시)
>
> **일시 장애 예외**: S3 일시 장애(ArchiveUnreachable/Timeout) 시
> `archive_ready.status=false`여도 `archive_key` 존재하면 ARCHIVED 유지
> (상세: calculate_phase() 참조)

### calculate_phase()

> **호출 주체**: WorkspaceController (WC)
> **역할**: conditions를 읽어 phase 계산 (순수 함수)

```python
def calculate_phase(
    conditions: dict,
    deleted_at: datetime | None,
    archive_key: str | None = None
) -> Phase:
    """Phase 계산 로직 (유일한 정의)

    Args:
        conditions: Condition 상태 딕셔너리 (빈 dict 허용)
        deleted_at: Soft delete 시각
        archive_key: Archive 경로 (일시 장애 시 ARCHIVED 유지 판정에 사용)

    Note:
        conditions가 빈 딕셔너리일 수 있으므로 기본값 병합 후 계산.
    """
    # 기본값 정의 (관측 전 상태)
    defaults = {
        "storage.volume_ready": {"status": False, "reason": "NotObserved"},
        "storage.archive_ready": {"status": False, "reason": "NotObserved"},
        "infra.container_ready": {"status": False, "reason": "NotObserved"},
        "policy.healthy": {"status": True, "reason": "NotObserved"},  # 관측 전엔 healthy
    }

    # 기본값과 실제값 병합
    def merge(key):
        return {**defaults.get(key, {}), **conditions.get(key, {})}

    cond = {k: merge(k) for k in defaults}

    # 삭제 처리
    if deleted_at:
        has_resources = (
            cond["storage.volume_ready"]["status"] or
            cond["infra.container_ready"]["status"] or
            cond["storage.archive_ready"]["status"]
        )
        return Phase.DELETING if has_resources else Phase.DELETED

    # 정책 위반 체크
    if not cond["policy.healthy"]["status"]:
        return Phase.ERROR

    # 정상 상태 판정
    if cond["infra.container_ready"]["status"] and cond["storage.volume_ready"]["status"]:
        return Phase.RUNNING
    if cond["storage.volume_ready"]["status"]:
        return Phase.STANDBY
    if cond["storage.archive_ready"]["status"]:
        return Phase.ARCHIVED

    # 일시 장애 시 archive_key 존재하면 ARCHIVED 유지
    if archive_key:
        reason = cond["storage.archive_ready"].get("reason", "")
        if reason in ["ArchiveUnreachable", "ArchiveTimeout"]:
            return Phase.ARCHIVED

    return Phase.PENDING
```

**기본값 정책**:
- `policy.healthy`: **true** (관측 전에는 건강하다고 가정)
- 나머지: **false** (리소스 존재를 가정하지 않음)

> **안전성**: 빈 conditions에도 기본값을 적용하여 KeyError 없이 안전하게 계산
>
> **일시 장애 안정성**: S3 일시 장애(ArchiveUnreachable/Timeout) 시
> archive_key가 존재하면 ARCHIVED 상태 유지

---

## Operation (진행 상태)

Operation은 **현재 진행 중인 작업**을 표현합니다.

| operation | 의미 |
|-----------|------|
| NONE | 안정 상태 |
| PROVISIONING | Volume 생성 중 |
| RESTORING | Archive → Volume 복원 중 |
| STARTING | Container 시작 중 |
| STOPPING | Container 정지 중 |
| ARCHIVING | Volume → Archive 중 |
| CREATE_EMPTY_ARCHIVE | 빈 Archive 생성 중 |
| DELETING | 전체 삭제 중 |

> **순수 데이터**: 전이 규칙은 State Machine 섹션 참조

---

## desired_state (목표)

| desired_state | 의미 |
|---------------|------|
| DELETED | 삭제 요청 |
| ARCHIVED | Archive만 유지 |
| STANDBY | Volume만 유지 |
| RUNNING | 실행 상태 |

> **PENDING 미포함**: PENDING은 phase로만 존재 (초기 상태)
> 사용자가 요청할 수 없음 - 삭제는 DELETED, 보관은 ARCHIVED 사용
>
> **API 소유**: desired_state는 API만 변경 가능

---

# State Machine

> **역할**: 상태 전이 규칙 (전이 순서, Operation 선택)

---

## Phase Level

Ordered State Machine에서 사용하는 레벨 정의입니다.

| Phase | Level |
|-------|-------|
| PENDING | 0 |
| ARCHIVED | 5 |
| STANDBY | 10 |
| RUNNING | 20 |

### 예외 (Ordered 미적용)

| Phase | 이유 |
|-------|------|
| ERROR | 정책 위반 (별도 축) |
| DELETING | 삭제 진행 중 |
| DELETED | 삭제 완료 |

---

## 전이 규칙

### step_up (상승)

```
PENDING(0) → ARCHIVED(5) → STANDBY(10) → RUNNING(20)
```

- 낮은 Level → 높은 Level
- 한 단계씩 순차 전이

### step_down (하강)

```
RUNNING(20) → STANDBY(10) → ARCHIVED(5)
```

- 높은 Level → 낮은 Level
- 한 단계씩 순차 전이

> **단조 경로**: 상승/하강 방향 혼합 없음

---

## Operation 선택

desired_state와 현재 Phase의 불일치를 해소하기 위한 Operation 선택 규칙입니다.

| 현재 Phase | desired | → operation |
|-----------|---------|-------------|
| PENDING | ARCHIVED | CREATE_EMPTY_ARCHIVE |
| PENDING | STANDBY+ | PROVISIONING |
| ARCHIVED | STANDBY+ | RESTORING |
| STANDBY | RUNNING | STARTING |
| STANDBY | ARCHIVED | ARCHIVING |
| RUNNING | STANDBY- | STOPPING |

> **STANDBY+**: STANDBY 또는 RUNNING (상승)
> **STANDBY-**: STANDBY 또는 ARCHIVED (하강)
> **Note**: PENDING은 desired_state가 아님 - 하강 시 ARCHIVED까지만

### 삭제 Operation

| 현재 Phase | 조건 | → operation |
|-----------|------|-------------|
| PENDING | operation=NONE | DELETING |
| ARCHIVED | operation=NONE | DELETING |
| ERROR | operation=NONE | DELETING |

> **RUNNING/STANDBY에서 삭제**: step_down으로 ARCHIVED 도달 후 DELETING
>
> **PENDING 예외**: PENDING은 리소스가 없으므로 deleted_at 설정 시 DELETED로 직접 전이 (DELETING 미거침)

---

## 상태 다이어그램

### 정상 흐름

```mermaid
stateDiagram-v2
    direction LR
    [*] --> PENDING: 생성 (새 workspace)
    [*] --> ARCHIVED: 생성 (기존 archive 존재)
    PENDING --> STANDBY: PROVISIONING
    PENDING --> ARCHIVED: CREATE_EMPTY_ARCHIVE
    ARCHIVED --> STANDBY: RESTORING
    STANDBY --> RUNNING: STARTING
    RUNNING --> STANDBY: STOPPING
    STANDBY --> ARCHIVED: ARCHIVING
```

### step_up 분기

```mermaid
flowchart TD
    D{현재 Phase?}
    D -->|PENDING + desired=STANDBY+| PR[PROVISIONING]
    D -->|PENDING + desired=ARCHIVED| CEA[CREATE_EMPTY_ARCHIVE]
    D -->|ARCHIVED| R[RESTORING]
    PR --> S[STANDBY]
    CEA --> A[ARCHIVED]
    R --> S
    S --> T[STARTING]
    T --> U[RUNNING]
```

### ERROR 흐름

```mermaid
stateDiagram-v2
    direction TB
    state "Any Phase" as any
    state "ERROR" as error

    any --> error: healthy=false
    error --> any: 수동 복구 (error_reason, error_count 리셋)

    note right of error
        Conditions는 실제 상태 유지
        operation=NONE 보장 (계약 #4)
    end note
```

---

## Phase × Operation 조합

유효한 조합만 정리합니다.

| Phase | operation | 의미 |
|-------|-----------|------|
| PENDING | NONE | 새 workspace |
| PENDING | PROVISIONING | Volume 생성 중 |
| PENDING | CREATE_EMPTY_ARCHIVE | 빈 Archive 생성 중 |
| ARCHIVED | NONE | Archive만 존재 |
| ARCHIVED | RESTORING | 복원 중 |
| STANDBY | NONE | Volume 준비됨 |
| STANDBY | STARTING | Container 시작 중 |
| STANDBY | ARCHIVING | 아카이브 중 |
| RUNNING | NONE | 실행 중 |
| RUNNING | STOPPING | 정지 중 |
| ERROR | NONE | 에러 상태 |
| DELETING | DELETING | 삭제 진행 중 |
| DELETING | NONE | Archive GC 대기 중 |

> **불변식 (C4)**: `Phase=ERROR → operation=NONE`

---

## desired_state 전환

### 허용 전환

| 현재 Phase | → ARCHIVED | → STANDBY | → RUNNING | → DELETED |
|-----------|------------|-----------|-----------|-----------|
| PENDING | ✓ | ✓ | ✓ | ✓ |
| ARCHIVED | - | ✓ | ✓ | ✓ |
| STANDBY | ✓ | - | ✓ | step_down 후 |
| RUNNING | ✓ | ✓ | - | step_down 후 |
| ERROR | 복구 후 | 복구 후 | 복구 후 | ✓ |

> **PENDING 미포함**: PENDING은 desired_state가 아님 (phase로만 존재)

### 409 Conflict

| 조건 | 결과 |
|------|------|
| operation ≠ NONE | 409 Conflict |

> **재시도 필요**: Auto-wake/TTL Manager는 409 시 exponential backoff 후 재시도

---

## 동작 시나리오

### 새 Workspace → RUNNING

```mermaid
sequenceDiagram
    U->>API: POST /workspaces
    API->>DB: desired_state = RUNNING
    API->>U: 201 (desired=RUNNING)
    WC->>WC: PROVISIONING (PENDING → STANDBY)
    WC->>WC: STARTING (STANDBY → RUNNING)
```

### Auto-wake (STANDBY → RUNNING)

```mermaid
sequenceDiagram
    U->>Proxy: GET /w/{id}/
    Proxy->>API: 내부 호출 (desired_state=RUNNING)
    API->>DB: desired_state = RUNNING
    WC->>WC: STARTING
```

### TTL Archive (STANDBY → ARCHIVED)

```mermaid
sequenceDiagram
    TTL->>API: 내부 호출 (desired_state=ARCHIVED)
    API->>DB: desired_state = ARCHIVED
    WC->>WC: ARCHIVING (STANDBY → ARCHIVED)
```

### 복원 (ARCHIVED → RUNNING)

```mermaid
sequenceDiagram
    U->>API: PATCH {desired: RUNNING}
    API->>DB: desired_state = RUNNING
    WC->>WC: RESTORING (ARCHIVED → STANDBY)
    WC->>WC: STARTING (STANDBY → RUNNING)
```

---

## 프록시 접속 동작

| Phase | 동작 |
|-------|------|
| RUNNING | 정상 연결 |
| STANDBY | Auto-wake → 연결 |
| ARCHIVED | 502 + "복원 필요" |
| PENDING | 502 + "시작 필요" |
| ERROR | 502 + "오류 발생" |

---

## TTL 자동 전환

| 전환 | TTL | 동작 |
|------|-----|------|
| RUNNING → STANDBY | standby_ttl (5분) | API 호출 |
| STANDBY → ARCHIVED | archive_ttl (1일) | API 호출 |

---

## 참조

- [00-contracts.md](./00-contracts.md) - 핵심 계약 (규칙)
- [03-schema.md](./03-schema.md) - DB 스키마 (Conditions SSOT)
- [04-control-plane.md#workspacecontroller](./04-control-plane.md#workspacecontroller) - WorkspaceController 구현
- [ADR-008](../adr/008-ordered-state-machine.md) - Ordered SM
- [ADR-009](../adr/009-status-operation-separation.md) - operation/op_id CAS
- [ADR-011](../adr/011-declarative-conditions.md) - Conditions 패턴
