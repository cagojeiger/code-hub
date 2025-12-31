# Workspace 상태 (M2)

> [README.md](./README.md)로 돌아가기

---

## 개요

**Ordered State Machine** + **Archive 속성 분리**

| 개념 | 설명 |
|------|------|
| Active (Ordered) | `PENDING(0) < STANDBY(10) < RUNNING(20)` |
| Archive (Flag) | `archive_key != NULL` → has_archive |
| Display (파생) | `PENDING + has_archive` → ARCHIVED |

> **계약 #5 준수**: Ordered State Machine ([00-contracts.md](./00-contracts.md#5-ordered-state-machine))

---

## 상태 정의

### Active 상태 (status)

| 상태 | Level | Container | Volume | 설명 |
|------|-------|-----------|--------|------|
| PENDING | 0 | - | - | 활성 리소스 없음 |
| STANDBY | 10 | - | ✅ | Volume만 존재 |
| RUNNING | 20 | ✅ | ✅ | 컨테이너 실행 중 |
| ERROR | -1 | (유지) | (유지) | 전환 실패 |
| DELETED | -2 | - | - | Soft-delete |

### 파생 상태 (Display)

| 조건 | Display |
|------|---------|
| PENDING + archive_key | **ARCHIVED** |
| PENDING + !archive_key | PENDING |
| 그 외 | status 그대로 |

---

## Operation 정의

| Operation | 전환 | 설명 |
|-----------|------|------|
| NONE | - | 안정 상태 |
| PROVISIONING | PENDING → STANDBY | 빈 Volume 생성 |
| RESTORING | PENDING(has_archive) → STANDBY | Archive → Volume |
| STARTING | STANDBY → RUNNING | Container 시작 |
| STOPPING | RUNNING → STANDBY | Container 정지 |
| ARCHIVING | STANDBY → PENDING | Volume → Archive |
| DELETING | PENDING/ERROR → DELETED | 전체 삭제 (operation=NONE 필수) |

### 상태 × Operation 조합

| status | operation | archive_key | 의미 |
|--------|-----------|-------------|------|
| PENDING | NONE | NULL | 새 workspace |
| PENDING | NONE | 있음 | ARCHIVED |
| PENDING | PROVISIONING | NULL | Volume 생성 중 |
| PENDING | RESTORING | 있음 | 복원 중 |
| STANDBY | NONE | - | Volume 준비됨 |
| STANDBY | STARTING | - | Container 시작 중 |
| STANDBY | ARCHIVING | - | 아카이브 중 |
| RUNNING | NONE | - | 실행 중 |
| RUNNING | STOPPING | - | 정지 중 |
| ERROR | NONE | - | ERROR 전환 시 operation 리셋, op_id 유지 (GC 보호) |

---

## 상태 다이어그램

### 정상 흐름

```mermaid
stateDiagram-v2
    direction LR
    [*] --> PENDING: 생성
    PENDING --> STANDBY: PROVISIONING/RESTORING
    STANDBY --> RUNNING: STARTING
    RUNNING --> STANDBY: STOPPING
    STANDBY --> PENDING: ARCHIVING
```

### step_up 분기

```mermaid
flowchart TD
    P[PENDING] --> A{archive_key?}
    A -->|있음| R[RESTORING]
    A -->|없음| V[PROVISIONING]
    R --> S[STANDBY]
    V --> S
    S --> T[STARTING]
    T --> U[RUNNING]
```

### ERROR 흐름

```mermaid
stateDiagram-v2
    direction TB
    state "Any status" as any
    any --> ERROR: is_terminal=true
    ERROR --> any: 수동 복구
```

---

## desired_state 설정

| 현재 status | → PENDING | → STANDBY | → RUNNING | Delete |
|-------------|-----------|-----------|-----------|--------|
| PENDING | - | ✓ | ✓ | ✓ |
| STANDBY | ✓ | - | ✓ | ✓ |
| RUNNING | ✓ | ✓ | - | ✓ |
| 전이 중 | 409 | 409 | 409 | 409 |
| ERROR | 복구 후 | 복구 후 | 복구 후 | ✓ |

> **계약 #4 준수**: Non-preemptive Operation ([00-contracts.md](./00-contracts.md#4-non-preemptive-operation))

---

## Operation 선택 규칙

| status | desired | archive_key | → Operation |
|--------|---------|-------------|-------------|
| PENDING | STANDBY/RUNNING | NULL | PROVISIONING |
| PENDING | STANDBY/RUNNING | 있음 | RESTORING |
| STANDBY | RUNNING | - | STARTING |
| STANDBY | PENDING | - | ARCHIVING |
| RUNNING | STANDBY/PENDING | - | STOPPING |
| PENDING/ERROR | - | deleted_at | DELETING (operation=NONE 필수) |

> **삭제 조건**: observed_status IN (PENDING, ERROR) AND operation = NONE
> - RUNNING/STANDBY에서 삭제 요청 시: step_down 완료 후 PENDING에서 삭제

---

## 프록시 접속 동작

| Display | 동작 |
|---------|------|
| RUNNING | 정상 연결 |
| STANDBY | Auto-wake (내부 서비스 레이어 API 호출) → 연결 |
| ARCHIVED | 502 + "복원 필요" |
| PENDING | 502 + "시작 필요" |
| ERROR | 502 + "오류 발생" |

> **계약 #3 준수**: Auto-wake 시 Proxy가 내부 서비스 레이어를 통해 API 호출 (desired_state=RUNNING)

---

## TTL 자동 전환

| 전환 | 트리거 | TTL Manager 동작 |
|------|--------|-----------------|
| RUNNING → STANDBY | standby_ttl (5분) | 내부 서비스 레이어 API 호출 (desired_state=STANDBY) |
| STANDBY → ARCHIVED | archive_ttl (1일) | 내부 서비스 레이어 API 호출 (desired_state=PENDING) |

> **계약 #3 준수**: TTL Manager가 내부 서비스 레이어를 통해 API 호출
> 상세: [04-control-plane.md#ttl-manager](./04-control-plane.md#ttl-manager)

---

## 주요 시나리오

### 새 Workspace → RUNNING

```mermaid
sequenceDiagram
    U->>API: POST /workspaces
    API->>U: 201 (desired=RUNNING)
    R->>R: PROVISIONING
    R->>R: STARTING
```

### Auto-wake (STANDBY → RUNNING)

```mermaid
sequenceDiagram
    U->>Proxy: GET /w/{id}/
    Proxy->>API: 내부 서비스 레이어 호출 (desired_state = RUNNING)
    API->>DB: desired_state = RUNNING
    R->>R: STARTING
```

### Manual Archive

```mermaid
sequenceDiagram
    U->>API: PATCH {desired: PENDING}
    R->>R: ARCHIVING
    Note right of R: archive_key 생성
```

---

## Known Issues

1. ~~**desired_state 경쟁**: API/TTL Manager/Proxy가 동시 변경 시 Last-Write-Wins~~
   - **해결됨**: 계약 #3에 따라 API만 desired_state 변경 가능 (TTL Manager, Proxy는 내부 서비스 레이어 통해 API 호출)
2. **순차 전이**: RUNNING → PENDING 직접 불가 (STOPPING → ARCHIVING)

---

## 참조

- [00-contracts.md](./00-contracts.md) - 핵심 계약
- [03-schema.md](./03-schema.md) - DB 스키마
- [04-control-plane.md](./04-control-plane.md) - Control Plane (SR, HM, TTL)
- [ADR-008](../adr/008-ordered-state-machine.md) - Ordered State Machine
