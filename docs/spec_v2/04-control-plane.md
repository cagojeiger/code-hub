# Control Plane (M2)

> Coordinator 프로세스 내 컴포넌트 및 정책 정의
>
> **의존**: [00-contracts.md](./00-contracts.md), [01-glossary.md](./01-glossary.md), [02-states.md](./02-states.md), [03-schema.md](./03-schema.md)

---

## 목차

1. [Coordinator](#coordinator)
2. [WorkspaceController](#workspacecontroller)
3. [TTL Manager](#ttl-manager)
4. [Events](#events)
5. [Activity](#activity)
6. [Error Policy](#error-policy)
7. [Limits](#limits)

---

## Coordinator

Coordinator는 모든 백그라운드 프로세스를 관리하는 **단일 리더 프로세스**입니다.

### 불변식

1. **Single Leader**: 동시에 하나의 Coordinator만 Active
2. **Automatic Failover**: 연결 끊김 시 자동 리더십 이전
3. **Single Writer**: 컴포넌트별 담당 컬럼 분리 (계약 #3)

### 아키텍처

```mermaid
flowchart TB
    subgraph Coordinator["Coordinator Process<br/>(pg_advisory_lock 보유)"]
        EL["EventListener<br/>(실시간)"]
        WC["WorkspaceController<br/>(10s 주기)"]
        TTL["TTL Manager<br/>(1m 주기)"]
        GC["Archive GC<br/>(1h 주기)"]
    end
```

### 컴포넌트 역할

| 컴포넌트 | 역할 |
|---------|------|
| EventListener | PG NOTIFY → Redis PUBLISH (CDC) |
| WorkspaceController | 리소스 관측 → conditions/phase 갱신 → 상태 수렴 |
| TTL Manager | TTL 만료 → desired_state 변경 |
| Archive GC | orphan archive 정리 |

> **계약 #2 준수**: Level-Triggered Reconciliation ([00-contracts.md](./00-contracts.md#2-level-triggered-reconciliation))

### Leader Election (PostgreSQL Session Lock)

| 특성 | 설명 |
|-----|------|
| 락 유형 | `pg_advisory_lock` (Session Level) |
| 락 해제 | DB 연결 끊기면 즉시 해제 |
| Failover | TCP timeout (수 초) |

> **목적**: Single Writer Principle ([#3](./00-contracts.md#3-single-writer-principle))을 보장하기 위한 단일 리더 실행

### 에러 처리

| 상황 | 동작 |
|------|------|
| DB 연결 끊김 | 리더십 포기 → 재연결 → 재획득 시도 |
| 개별 컴포넌트 에러 | 해당 tick 스킵 → 다음 tick 재시도 |
| 전체 루프 에러 | 리더십 포기 → 재획득 시도 |

---

## WorkspaceController

WorkspaceController는 리소스 **관측**, 상태 **판정**, 상태 **수렴**을 담당하는 **단일 컴포넌트**입니다.

- **관측**(observe): 리소스 상태 → conditions 갱신
- **판정**(judge): conditions → phase 계산
- **제어**(control): phase ≠ desired → operation 실행

> **계약 준수**: [#1 Reality vs DB](./00-contracts.md#1-reality-vs-db-진실의-원천), [#2 Level-Triggered](./00-contracts.md#2-level-triggered-reconciliation)

### 역할: Observer + Controller + Judge

| 역할 | 입력 | 출력 |
|------|------|------|
| **Observer** | Container/Volume/Archive Provider | conditions |
| **Judge** | conditions, deleted_at, archive_key | phase |
| **Controller** | phase, desired_state | operation 실행 |

### 핵심 원칙

1. **진실(Reality) = 실제 리소스**: Container/Volume/Archive의 실제 존재 여부가 진실
2. **DB = Last Observed Truth**: DB는 마지막 관측치일 뿐
3. **단일 컴포넌트**: 관측+판정+제어를 단일 트랜잭션으로 처리 (원자성 보장)
4. **Level-Triggered**: 이벤트가 아닌 현재 DB 상태 기준

### 불변식

1. **Non-preemptive**: `operation != NONE`이면 다른 operation 시작 불가 (계약 #4)
2. **CAS 선점**: operation 시작 시 `WHERE operation = 'NONE'` 조건 사용
3. **ERROR 원자성**: 에러 시 `phase=ERROR, operation=NONE, error_reason` 단일 트랜잭션

### 입출력

**읽기**: desired_state, operation, op_started_at, error_count, archive_key, deleted_at, Container/Volume/Archive Provider

**쓰기**: conditions, observed_at, phase, operation, op_started_at, op_id, archive_key, error_count, error_reason, home_ctx (Single Writer)

### 주기

| 상태 | 주기 | 이유 |
|------|------|------|
| 기본 | 10s | 일반 폴링 |
| operation != NONE | 2s | 적응형 (빠른 완료 감지) |

### Reconcile 흐름

```mermaid
flowchart TB
    START["reconcile(ws)"]

    subgraph Observe["1. Observe"]
        OBS["observe_resources()"]
        COND["conditions 갱신"]
    end

    subgraph Judge["2. Judge"]
        CALC["calculate_phase(conditions)"]
        INV["check_invariants()"]
        HEALTH["policy.healthy 설정"]
    end

    subgraph Control["3. Control"]
        PHASE{"phase == ERROR?"}
        OP{"operation != NONE?"}
        CONV{"phase == desired?"}
        CHECK["완료/timeout 체크"]
        PLAN["Plan: operation 결정"]
        EXEC["Execute: 작업 실행"]
    end

    SAVE["단일 트랜잭션 저장<br/>(conditions, phase, operation, ...)"]

    START --> OBS --> COND --> CALC --> INV --> HEALTH --> PHASE
    PHASE -->|Yes| SAVE
    PHASE -->|No| OP
    OP -->|Yes| CHECK --> SAVE
    OP -->|No| CONV
    CONV -->|Yes| SAVE
    CONV -->|No| PLAN --> EXEC --> SAVE
```

> **원자성**: 관측/판정/제어 결과를 단일 트랜잭션으로 저장
> **phase 기반 skip**: phase=ERROR일 때 reconcile 제외 (단, desired=DELETED는 허용)

### Conditions 갱신 규칙

| Condition | 관측 방법 | status=true 조건 |
|-----------|----------|-----------------|
| `storage.volume_ready` | Volume Provider 호출 | Volume 존재 |
| `storage.archive_ready` | S3 HEAD 요청 (archive_key 존재 시) | Archive 접근 가능 |
| `infra.container_ready` | Container Provider 호출 | Container running |
| `policy.healthy` | 불변식 확인 | 불변식 위반 없음 |

> **policy.healthy 규칙**: [03-schema.md#policy.healthy](./03-schema.md#policyhealthyfalse-조건)

### 불변식 위반 감지

| 위반 유형 | 조건 | 처리 |
|----------|------|------|
| ContainerWithoutVolume | container_ready ∧ !volume_ready | policy.healthy = {status: false, reason: "ContainerWithoutVolume"} |
| ArchiveAccessError | archive_ready.reason ∈ {Corrupted, Expired, NotFound} | policy.healthy = {status: false, reason: "ArchiveAccessError"} |

### Operation 결정 규칙

> **정의**: [02-states.md#operation-선택](./02-states.md#operation-선택)
>
> **계약 준수**: [#4 Non-preemptive](./00-contracts.md#4-non-preemptive-operation), [#5 Ordered SM](./00-contracts.md#5-ordered-state-machine)

### 완료 조건

| Operation | 완료 조건 (conditions 기반) |
|-----------|---------------------------|
| PROVISIONING | volume_ready == true |
| RESTORING | volume_ready == true AND restore_marker == archive_key |
| STARTING | container_ready == true |
| STOPPING | container_ready == false |
| ARCHIVING | volume_ready == false AND archive_key != NULL |
| CREATE_EMPTY_ARCHIVE | archive_ready == true AND archive_key != NULL |

> 완료 시: phase 재계산 → `operation = NONE`, `error_count = 0`, `error_reason = NULL`

### CREATE_EMPTY_ARCHIVE

PENDING에서 ARCHIVED로 직접 전이 시 사용 (Ordered SM 단조 경로):

1. 빈 tar.zst 생성 (메모리, ~50 bytes)
2. S3 업로드 (`{workspace_id}/{op_id}/home.tar.zst`)
3. archive_key 설정
4. 관측 → `archive_ready = true`
5. conditions 확인 → phase = ARCHIVED, operation = NONE

> **단조 경로**: PENDING(0) → ARCHIVED(5) 직접 전이 (step_up)
> **결과**: 빈 Archive 생성 (복원 시 빈 Volume으로 시작)

**RESTORING 완료 조건** (계약 #7):
1. Volume 존재 관측 → `volume_ready = true`
2. `volume_ready + restore_marker == archive_key` 확인 → 복원 완료, phase = STANDBY

> **ARCHIVING 순서 보장** (계약 #8):
> 1. archive() 호출 후 archive_key를 DB에 저장
> 2. delete_volume() 호출
>
> 이 순서를 반드시 준수하여 데이터 유실 방지. ([Ordering Guarantee](./00-contracts.md#8-ordering-guarantee-역순-금지))

### Timeout / 재시도

> **원칙**: Timeout 초과 또는 재시도 3회 초과 시 단말 에러
> **구체적 값**: 코드에서 정의 (구현 세부)

### ERROR 전환 규칙 (계약 #4)

WC가 에러 감지 시 **단일 트랜잭션**으로 원자적 전환:

```sql
UPDATE workspaces SET
    phase = 'ERROR',
    operation = 'NONE',
    error_reason = 'ActionFailed',
    error_count = error_count + 1
WHERE id = ? AND operation != 'NONE'
```

| 단계 | 동작 |
|------|------|
| 1 | 에러 감지 (timeout, 재시도 초과, 불변식 위반 등) |
| 2 | `phase=ERROR, operation=NONE, error_reason, error_count` 원자적 설정 |
| 3 | `op_id` 유지 (GC 보호) |

> **원자성 보장**: 크래시 시에도 에러 유실 없음 (단일 트랜잭션)
> **CAS 실패 처리**: operation 선점 CAS 실패 시 다음 reconcile 사이클에서 재시도

---

## TTL Manager

TTL Manager는 비활성 워크스페이스의 TTL을 체크하고 desired_state를 변경합니다.

### TTL 종류

| TTL | 대상 Phase | 트리거 | 동작 |
|-----|-----------|--------|------|
| standby_ttl | RUNNING | WebSocket 종료 + idle 타이머 만료 | desired_state = STANDBY |
| archive_ttl | STANDBY | last_access_at 기준 경과 | desired_state = **ARCHIVED** |

> **ARCHIVED로 변경**: 기존 PENDING 대신 ARCHIVED로 설정하여 Archive 보존

### 입출력

**읽기**: DB (phase, operation, TTL 컬럼, last_access_at), Redis (ws_conn, idle_timer)

**쓰기**: (없음 - 내부 서비스 레이어를 통해 API 호출)

> **계약 #3 준수**: TTL Manager는 desired_state를 직접 변경하지 않고, 내부 서비스 레이어를 통해 API 호출

### Standby TTL 체크 규칙

| 조건 | 결과 |
|------|------|
| phase != RUNNING | skip |
| operation != NONE | skip |
| ws_conn > 0 | skip (활성 연결) |
| idle_timer 존재 | skip (5분 대기 중) |
| 위 조건 모두 통과 | API 호출: desired_state = STANDBY |

### Archive TTL 체크 규칙

| 조건 | 결과 |
|------|------|
| phase != STANDBY | skip |
| operation != NONE | skip |
| NOW() - last_access_at <= archive_ttl_seconds | skip |
| 위 조건 모두 통과 | API 호출: desired_state = **ARCHIVED** |

> **ARCHIVED 유지**: desired_state=ARCHIVED로 설정되므로 Archive 보존 (M1 해결)

---

## Activity

WebSocket 연결 기반으로 워크스페이스 활동을 추적합니다.

### Redis 키

| 키 | 타입 | 설명 |
|----|------|------|
| `ws_conn:{workspace_id}` | Integer | WebSocket 연결 수 |
| `idle_timer:{workspace_id}` | String (TTL 5분) | idle 타이머 |

### Proxy 동작

| 이벤트 | 동작 |
|--------|------|
| WebSocket Connect | INCR ws_conn, DEL idle_timer |
| WebSocket Disconnect | DECR ws_conn, count=0이면 SETEX idle_timer 300 |

### Idle 타이머 흐름

```mermaid
sequenceDiagram
    participant B as Browser
    participant P as Proxy
    participant R as Redis
    participant T as TTL Manager

    B->>P: WebSocket Connect
    P->>R: INCR ws_conn:{id}
    P->>R: DEL idle_timer:{id}

    Note over B,P: 사용 중...

    B->>P: WebSocket Close
    P->>R: DECR ws_conn:{id}
    Note over P,R: count == 0
    P->>R: SETEX idle_timer 300

    Note over R: 5분 후 자동 만료

    T->>R: TTL Manager 체크
    R-->>T: ws_conn=0, no timer
    Note over T: desired_state = STANDBY
```

### last_access_at 갱신

| 시점 | 주체 | 값 |
|------|------|---|
| workspace 생성 | API | NOW() |
| STOPPING 완료 | WorkspaceController | NOW() |

---

## Events

상태 변경 시 UI에 실시간 알림을 전달합니다 (CDC 패턴).

### 이벤트 전달 흐름

```mermaid
sequenceDiagram
    participant W as Writer (API, Reconciler, ...)
    participant DB as PostgreSQL
    participant C as Coordinator (EventListener)
    participant Redis as Redis Pub/Sub
    participant API as Control Plane
    participant UI as Dashboard

    W->>DB: UPDATE workspaces SET ...
    Note over DB: Trigger 실행
    DB-->>C: NOTIFY workspace_changes
    C->>Redis: PUBLISH workspace:{id}
    Redis-->>API: 메시지 수신
    API-->>UI: SSE event
```

### 이벤트 발행 구조

| 구분 | 값 |
|------|---|
| 트리거 대상 | workspaces 테이블 UPDATE |
| 감시 컬럼 | phase, operation, error_reason |
| 발행 | pg_notify('workspace_changes', payload) |

### SSE 엔드포인트

```
GET /api/v1/workspaces/{id}/events
Accept: text/event-stream
```

| 항목 | 설명 |
|------|------|
| Heartbeat | 주기적 전송 (구체 값은 코드 정의) |
| 재연결 | 클라이언트 자동 재연결 |

### 이벤트 타입

| 타입 | 발행 시점 |
|------|----------|
| state_changed | operation 시작/완료 |
| error | 에러 발생 |
| heartbeat | 30초마다 |

---

## Error Policy

### error_reason 값

| error_reason | is_terminal | 설명 |
|--------------|-------------|------|
| Timeout | 즉시 | 작업 시간 초과 |
| RetryExceeded | error_count 기반 | 재시도 한도 초과 |
| ActionFailed | 재시도 후 | Actuator 호출 실패 |
| DataLost | 즉시 | 복구 불가 데이터 손실 |
| Unreachable | 재시도 후 | 리소스 접근 불가 |
| ImagePullFailed | 즉시 | 컨테이너 이미지 가져오기 실패 |
| ContainerWithoutVolume | 즉시 | 불변식 위반 |
| ArchiveCorrupted | 즉시 | Archive 체크섬 불일치 |

**단말 에러(is_terminal) 판정**:
```python
TERMINAL_REASONS = {"Timeout", "DataLost", "ImagePullFailed", "ContainerWithoutVolume", "ArchiveCorrupted"}
is_terminal = error_reason in TERMINAL_REASONS or error_count >= MAX_RETRY
```

> **상세 메시지**: 로그에서 확인 (DB 미저장)
> **상세 정의**: 코드에서 enum으로 정의 (error_types.py)

### 에러 처리 흐름

WorkspaceController가 관측/판정/제어를 단일 트랜잭션으로 처리:

1. WC: 에러 감지 → `phase=ERROR, operation=NONE, error_reason, error_count` 원자적 설정

> **단일 컴포넌트**: 관측+판정+제어 통합으로 2단계 전환 제거

### 재시도 책임 분리

| 레벨 | 역할 |
|------|------|
| Job 내부 | 일시적 오류 재시도 |
| WorkspaceController | Operation 레벨 재시도 |

> 각 레벨 3회씩, 최대 9회까지 가능 (의도된 동작)

### GC 보호

| phase | GC 동작 | 이유 |
|-------|---------|------|
| ERROR | 보호 (삭제 안 함) | 복구 시 archive 필요 |
| DELETED | 삭제 대상 | soft-delete workspace |

> **계약 준수**: [#9 GC Separation & Protection](./00-contracts.md#9-gc-separation--protection)
> **phase 기반**: GC 보호는 phase=ERROR 기준

### ERROR 복구

ERROR 전환 시 operation이 이미 NONE으로 리셋되므로, 복구 시 2개 필드만 리셋:

```mermaid
sequenceDiagram
    participant Admin as 관리자
    participant DB as Database
    participant WC as WorkspaceController

    Note over Admin,DB: operation은 이미 NONE (ERROR 전환 시 리셋됨)
    Admin->>DB: error_reason = NULL
    Admin->>DB: error_count = 0
    WC->>WC: reconcile 시 관측 → phase 재계산
    Note over WC: 단일 트랜잭션으로 conditions+phase 갱신
```

| 필드 | 리셋 필요 | 이유 |
|------|----------|------|
| error_reason | O | 에러 정보 초기화 |
| error_count | O | 재시도 횟수 초기화 |
| operation | X | ERROR 전환 시 이미 NONE |
| op_id | X | GC 보호용으로 유지 |
| phase | X | WC가 다음 reconcile에서 재계산 |
| conditions | X | WC가 reconcile에서 갱신 |

---

## Limits

동시에 실행 가능한 워크스페이스 수를 제한합니다.

### 제한 유형

| 제한 | 기본값 | 설명 |
|------|--------|------|
| max_running_per_user | 2 | 사용자당 동시 RUNNING |
| max_running_global | 100 | 시스템 전체 동시 RUNNING |

### 체크 시점

| 시점 | 동작 |
|------|------|
| API: desired_state = RUNNING | 제한 체크 후 설정 |
| Proxy: Auto-wake 트리거 | 제한 체크 후 진행 |

### 제한 초과 시 동작

| 상황 | 응답 |
|------|------|
| API 요청 | 429 Too Many Requests |
| Auto-wake | 502 + 안내 페이지 (실행 중인 워크스페이스 목록) |

> **Soft Limit**: Race condition으로 약간 초과 가능 (허용)

---

## Known Issues

1. ~~**관측 지연**: 최대 30초~~
   - **해결됨**: 단일 WC로 통합, operation 진행 중 2초 주기
2. **Operation 중단 불가**: 시작 후 취소 불가, 완료까지 대기
3. **순차적 전이**: RUNNING → PENDING 직접 불가 (STOPPING → ARCHIVING 순차)
4. ~~**재시도 간격 고정**: 지수 백오프 미적용~~ → M2에서 구현 예정
5. ~~**desired_state 경쟁**: API/TTL Manager/Proxy 동시 변경 시 Last-Write-Wins~~
   - **해결됨**: 계약 #3에 따라 API만 desired_state 변경 가능
6. **ERROR 자동 복구 불가**: 관리자 수동 개입 필요 (error_reason, error_count 리셋)
7. ~~**observed_status에 ERROR 포함**: 리소스 관측과 정책 판정 혼재~~
   - **해결됨**: Conditions 패턴으로 분리 (ADR-011)
8. ~~**ERROR 전환 2단계**: OC→RO 간접 통신으로 크래시 시 에러 유실 가능~~
   - **해결됨**: WC가 phase=ERROR 원자적 설정 (단일 트랜잭션)
9. ~~**Phase 일시적 불일치**: conditions 변경 후 reconcile 전까지 phase가 stale~~
   - **해결됨**: WC가 conditions+phase를 단일 트랜잭션으로 저장

---

## 참조

- [00-contracts.md](./00-contracts.md) - 핵심 계약
- [02-states.md](./02-states.md) - 상태 정의
- [03-schema.md](./03-schema.md) - DB 스키마
- [ADR-011](../adr/011-declarative-conditions.md) - Conditions 기반 상태 표현
